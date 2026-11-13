"""Main orchestrator for serverless EWR/NYC flight deal & mistake-fare tracking."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Optional

from src.config import TrackerConfig, load_config
from src.explore import ExploreScanner
from src.models import FlightDeal
from src.notifier import Notifier
from src.pinned import PinnedWatcher
from src.secret_flying import SecretFlyingScanner
from src.state import StateManager

# Configure clean logging
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("tracker")


def run_tracker(
    config_path: str = "config.yaml",
    dry_run: Optional[bool] = None,
) -> list[FlightDeal]:
    """Execute full flight deal tracking workflow."""
    if dry_run is None:
        dry_run = os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes")

    logger.info("=" * 65)
    logger.info("🛫 Starting EWR/NYC Serverless Flight Tracker")
    logger.info(f"    Mode: {'DRY RUN (no external alerts dispatched)' if dry_run else 'LIVE RUN'}")
    logger.info("=" * 65)

    # 1. Load configuration
    config = load_config(config_path)
    logger.info(
        f"Loaded config: origins={config.origins}, currency={config.currency}, "
        f"drop_threshold={config.drop_threshold_pct}%, baseline_window={config.baseline_window_days}d, "
        f"max_layover={config.max_layover_hours}h"
    )

    # 2. Initialize state manager and notifier
    state_mgr = StateManager(
        bucket_size=config.price_bucket_size,
        retention_days=config.history_retention_days,
    )
    notifier = Notifier(dry_run=dry_run)

    all_candidate_deals: list[FlightDeal] = []

    # 3. Google Flights Explore Scan (open-ended discovery from EWR prioritized)
    logger.info("\n>>> [1/3] Scanning Google Flights Explore (GetExploreDestinations)...")
    explore_scanner = ExploreScanner(config, state_mgr)
    explore_deals = explore_scanner.scan_all_origins()
    logger.info(f"Explore scan completed: found {len(explore_deals)} candidates.")
    all_candidate_deals.extend(explore_deals)

    # 4. Pinned Watches (booked itineraries & reprice eCredit monitor)
    if config.pinned_watches:
        logger.info(f"\n>>> [2/3] Checking {len(config.pinned_watches)} Pinned Route Watches...")
        pinned_watcher = PinnedWatcher(
            watches=config.pinned_watches,
            currency=config.currency,
            max_layover_hours=config.max_layover_hours,
        )
        pinned_deals = pinned_watcher.check_all()
        logger.info(f"Pinned watches check completed: {len(pinned_deals)} qualified deals.")
        all_candidate_deals.extend(pinned_deals)
    else:
        logger.info("\n>>> [2/3] No pinned watches configured in config.yaml.")

    # 5. Secret Flying NYC Mistake-Fare Feed
    if config.secret_flying_enabled:
        logger.info("\n>>> [3/3] Scanning Secret Flying NYC feed for mistake fares...")
        sf_scanner = SecretFlyingScanner(keywords=config.secret_flying_keywords)
        sf_deals = sf_scanner.scan()
        logger.info(f"Secret Flying scan completed: {len(sf_deals)} candidate deals found.")
        all_candidate_deals.extend(sf_deals)
    else:
        logger.info("\n>>> [3/3] Secret Flying feed disabled in config.yaml.")

    # 6. Deduplication and Alert Dispatch
    logger.info("\n>>> Evaluating deals against seen_deals.json...")
    new_alert_deals: list[FlightDeal] = []
    skipped_duplicates = 0

    for deal in all_candidate_deals:
        if state_mgr.is_deal_seen(deal):
            skipped_duplicates += 1
            continue

        new_alert_deals.append(deal)
        state_mgr.mark_deal_seen(deal)

    logger.info(
        f"Evaluation summary: {len(all_candidate_deals)} total candidates, "
        f"{skipped_duplicates} duplicate/already-alerted, {len(new_alert_deals)} new deals."
    )

    # Dispatch alerts for new deals
    if new_alert_deals:
        logger.info(f"\n>>> Dispatching {len(new_alert_deals)} new deal alerts...")
        for deal in new_alert_deals:
            notifier.notify(deal)
    else:
        logger.info("No new non-duplicate deals to alert in this run.")

    # 7. Persist updated state to disk
    state_mgr.save()

    # 8. Output stdout report of top 5 cheapest international round-trip deals
    print_top_deals_report(all_candidate_deals, config.currency)

    return all_candidate_deals


def print_top_deals_report(deals: list[FlightDeal], currency: str = "USD") -> None:
    """Print clean stdout summary table of top 5 cheapest international round-trip deals."""
    print("\n" + "=" * 78)
    print(" 🏆 TOP 5 CHEAPEST INTERNATIONAL ROUND-TRIP DEALS FOUND")
    print("=" * 78)

    # Filter deals with a valid price and sort ascending
    valid_deals = [d for d in deals if d.price and d.price > 0]
    valid_deals.sort(key=lambda d: d.price)

    # De-duplicate by destination name so top 5 displays diverse destinations
    unique_top: list[FlightDeal] = []
    seen_dests: set[str] = set()
    for d in valid_deals:
        dest_key = d.destination.upper()
        if dest_key not in seen_dests:
            seen_dests.add(dest_key)
            unique_top.append(d)
        if len(unique_top) >= 5:
            break

    if not unique_top:
        print("  No international deals found matching price ceilings in this run.")
        print("=" * 78 + "\n")
        return

    header = f"{'#':<3} {'ROUTE':<12} {'DESTINATION':<24} {'PRICE':<10} {'AIRLINE':<20} {'DATES'}"
    print(header)
    print("-" * 78)

    for idx, d in enumerate(unique_top, start=1):
        route = f"{d.origin} -> {d.destination}"
        dest_display = (d.destination_name or d.destination)[:22]
        price_str = f"${d.price:,.0f} {d.currency}"
        airline_str = (d.airline or "Various")[:18]
        dates_str = f"{d.departure_date} to {d.return_date}"
        print(f"{idx:<3} {route:<12} {dest_display:<24} {price_str:<10} {airline_str:<20} {dates_str}")

    print("=" * 78)
    print("Links:")
    for idx, d in enumerate(unique_top, start=1):
        print(f" [{idx}] {d.origin}->{d.destination} (${d.price:,.0f}): {d.deep_link}")
    print("=" * 78 + "\n")


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="Serverless EWR/NYC International Flight Deal Tracker"
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to YAML configuration file (default: config.yaml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without dispatching notifications to webhooks (DRY_RUN=1)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    run_tracker(config_path=args.config, dry_run=args.dry_run or None)


if __name__ == "__main__":
    main()
