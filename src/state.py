"""State persistence for deduplication and rolling baseline price history."""

from __future__ import annotations

import json
import logging
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from src.models import FlightDeal

logger = logging.getLogger(__name__)

SEEN_DEALS_FILE = Path("data/seen_deals.json")
PRICE_HISTORY_FILE = Path("data/price_history.json")


class StateManager:
    """Manages zero-database state persistence stored in Git."""

    def __init__(
        self,
        seen_deals_path: Path | str = SEEN_DEALS_FILE,
        price_history_path: Path | str = PRICE_HISTORY_FILE,
        bucket_size: float = 25.0,
        retention_days: int = 180,
    ):
        self.seen_deals_path = Path(seen_deals_path)
        self.price_history_path = Path(price_history_path)
        self.bucket_size = bucket_size
        self.retention_days = retention_days

        self.seen_deals: dict[str, dict[str, Any]] = {}
        self.price_history: dict[str, list[dict[str, Any]]] = {}

        self.load()

    def load(self) -> None:
        """Load state from JSON files if they exist."""
        if self.seen_deals_path.is_file():
            try:
                with open(self.seen_deals_path, "r", encoding="utf-8") as f:
                    self.seen_deals = json.load(f)
            except Exception as e:
                logger.error(f"Error loading {self.seen_deals_path}: {e}")
                self.seen_deals = {}
        else:
            self.seen_deals = {}

        if self.price_history_path.is_file():
            try:
                with open(self.price_history_path, "r", encoding="utf-8") as f:
                    self.price_history = json.load(f)
            except Exception as e:
                logger.error(f"Error loading {self.price_history_path}: {e}")
                self.price_history = {}
        else:
            self.price_history = {}

    def is_deal_seen(self, deal: FlightDeal) -> bool:
        """Check if a flight deal has already been alerted in its price bucket."""
        key = deal.deterministic_key(self.bucket_size)
        return key in self.seen_deals

    def mark_deal_seen(self, deal: FlightDeal) -> None:
        """Mark a deal as alerted so duplicate notifications are suppressed."""
        key = deal.deterministic_key(self.bucket_size)
        now_iso = datetime.now(timezone.utc).isoformat()
        if key in self.seen_deals:
            self.seen_deals[key]["last_seen"] = now_iso
            self.seen_deals[key]["price"] = deal.price
        else:
            self.seen_deals[key] = {
                "first_seen": now_iso,
                "last_seen": now_iso,
                "origin": deal.origin,
                "destination": deal.destination,
                "departure_date": deal.departure_date,
                "return_date": deal.return_date,
                "price": deal.price,
                "currency": deal.currency,
                "airline": deal.airline,
                "deal_type": deal.deal_type.value,
                "source": deal.source,
            }

    def record_price(
        self,
        origin: str,
        destination: str,
        price: float,
        dep_month: Optional[str] = None,
    ) -> None:
        """Record an observed price for rolling baseline calculation."""
        route = f"{origin.upper()}-{destination.upper()}"
        now_ts = int(time.time())
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        if route not in self.price_history:
            self.price_history[route] = []

        # Avoid storing duplicate prices for the same route and departure month on the same day
        today_records = [
            r for r in self.price_history[route]
            if r.get("date") == date_str and r.get("dep_month") == dep_month
        ]
        if today_records:
            # Update today's lowest observed price
            today_records[0]["price"] = min(today_records[0]["price"], price)
            today_records[0]["timestamp"] = now_ts
        else:
            rec: dict[str, Any] = {"timestamp": now_ts, "date": date_str, "price": price}
            if dep_month:
                rec["dep_month"] = dep_month
            self.price_history[route].append(rec)

    def get_rolling_baseline(
        self,
        origin: str,
        destination: str,
        days: int = 90,
        min_observations: int = 3,
        dep_month: Optional[str] = None,
    ) -> Optional[float]:
        """Compute the rolling median baseline price for a route over an extended window (e.g. 90 days).

        If dep_month is supplied, first attempts to use seasonal data for that departure month.
        If seasonal data has fewer than min_observations, falls back to the general route history.
        Returns None if total observations < min_observations (to prevent noisy premature drop alerts).
        """
        route = f"{origin.upper()}-{destination.upper()}"
        history = self.price_history.get(route, [])
        if not history:
            return None

        cutoff_ts = int(time.time()) - (days * 86400)
        recent_records = [
            r for r in history if r.get("timestamp", 0) >= cutoff_ts
        ]

        # If not enough records within the cutoff window, check all available records up to retention
        if not recent_records:
            recent_records = history

        # Tier 1: Try seasonal slice for departure month (e.g. 2027-01)
        if dep_month:
            seasonal_prices = [
                r["price"] for r in recent_records if r.get("dep_month") == dep_month
            ]
            if len(seasonal_prices) >= min_observations:
                return float(statistics.median(seasonal_prices))

        # Tier 2: Overall route history within rolling window
        route_prices = [r["price"] for r in recent_records]
        if len(route_prices) < min_observations:
            # Insufficient observations to form a dependable baseline
            return None

        return float(statistics.median(route_prices))

    def evaluate_price_drop(
        self,
        origin: str,
        destination: str,
        current_price: float,
        days: int = 90,
        min_observations: int = 3,
        dep_month: Optional[str] = None,
    ) -> tuple[Optional[float], Optional[float]]:
        """Calculate drop percentage and baseline for a given price.

        Returns (drop_pct, baseline_price).
        """
        baseline = self.get_rolling_baseline(
            origin,
            destination,
            days=days,
            min_observations=min_observations,
            dep_month=dep_month,
        )
        if baseline is None or baseline <= 0:
            return None, None

        if current_price < baseline:
            drop_pct = round(((baseline - current_price) / baseline) * 100.0, 1)
            return drop_pct, baseline

        return 0.0, baseline

    def prune_old_state(self, retention_days: Optional[int] = None) -> None:
        """Prune old price history and expired deals to maintain compact JSON files."""
        days = retention_days or self.retention_days
        cutoff_ts = int(time.time()) - (days * 86400)
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Prune price history older than retention window
        for route, records in list(self.price_history.items()):
            pruned = [r for r in records if r.get("timestamp", 0) >= cutoff_ts]
            if pruned:
                self.price_history[route] = pruned
            else:
                del self.price_history[route]

        # Prune seen deals whose return_date has already passed
        expired_keys = []
        for key, deal in self.seen_deals.items():
            ret_date = deal.get("return_date")
            if ret_date and ret_date < today_str:
                expired_keys.append(key)

        for k in expired_keys:
            del self.seen_deals[k]

        logger.debug(
            f"State pruned: {len(expired_keys)} expired deals removed."
        )

    def save(self) -> None:
        """Save state cleanly back to disk."""
        self.prune_old_state()

        self.seen_deals_path.parent.mkdir(parents=True, exist_ok=True)
        self.price_history_path.parent.mkdir(parents=True, exist_ok=True)

        # Write seen_deals.json
        tmp_deals = self.seen_deals_path.with_suffix(".tmp")
        with open(tmp_deals, "w", encoding="utf-8") as f:
            json.dump(self.seen_deals, f, indent=2, sort_keys=True)
        tmp_deals.replace(self.seen_deals_path)

        # Write price_history.json
        tmp_hist = self.price_history_path.with_suffix(".tmp")
        with open(tmp_hist, "w", encoding="utf-8") as f:
            json.dump(self.price_history, f, indent=2, sort_keys=True)
        tmp_hist.replace(self.price_history_path)

        logger.info(
            f"Saved state: {len(self.seen_deals)} seen deals, "
            f"{len(self.price_history)} tracked routes in price history."
        )
