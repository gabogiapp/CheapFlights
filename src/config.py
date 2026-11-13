"""Configuration loader and region mapping logic."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from src.models import PinnedWatch

logger = logging.getLogger(__name__)

# Standard regions recognized by the tracker
REGION_EUROPE = "Europe"
REGION_CARIBBEAN = "Caribbean / Mexico / Central America"
REGION_SOUTH_AMERICA = "South America"
REGION_ASIA = "Asia / Japan"

# Mapping of countries / territories to regional categories
EUROPE_COUNTRIES = {
    "albania", "andorra", "austria", "belarus", "belgium", "bosnia and herzegovina",
    "bulgaria", "croatia", "cyprus", "czech republic", "czechia", "denmark",
    "estonia", "finland", "france", "germany", "greece", "hungary", "iceland",
    "ireland", "italy", "kosovo", "latvia", "liechtenstein", "lithuania",
    "luxembourg", "malta", "moldova", "monaco", "montenegro", "netherlands",
    "north macedonia", "norway", "poland", "portugal", "romania", "san marino",
    "serbia", "slovakia", "slovenia", "spain", "sweden", "switzerland", "turkey",
    "türkiye", "ukraine", "united kingdom", "vatican city", "england", "scotland",
    "wales", "northern ireland",
}

CARIBBEAN_CENTRAL_AMERICA_COUNTRIES = {
    "mexico", "costa rica", "panama", "belize", "guatemala", "honduras",
    "el salvador", "nicaragua", "bahamas", "dominican republic", "jamaica",
    "puerto rico", "aruba", "curaçao", "curacao", "sint maarten", "st. martin",
    "saint martin", "barbados", "cayman islands", "turks and caicos", "bermuda",
    "u.s. virgin islands", "virgin islands", "british virgin islands", "guadeloupe",
    "martinique", "antigua and barbuda", "saint lucia", "st. lucia",
    "trinidad and tobago", "grenada", "saint kitts and nevis", "cuba", "haiti",
}

SOUTH_AMERICA_COUNTRIES = {
    "brazil", "argentina", "colombia", "peru", "chile", "ecuador", "bolivia",
    "paraguay", "uruguay", "guyana", "suriname", "venezuela", "french guiana",
}

ASIA_COUNTRIES = {
    "japan", "south korea", "korea", "china", "taiwan", "hong kong", "macau",
    "singapore", "thailand", "vietnam", "philippines", "indonesia", "malaysia",
    "india", "cambodia", "laos", "nepal", "sri lanka", "maldives", "mongolia",
    "myanmar", "bangladesh", "kazakhstan", "uzbekistan",
}


@dataclass
class ExploreScanConfig:
    """Settings for scanning flexible Google Flights explore windows."""

    window_weeks: list[int] = field(default_factory=lambda: [1, 2])
    months_ahead: int = 6
    sample_interval_days: int = 21
    max_dates_per_run: int = 2


@dataclass
class TrackerConfig:
    """Full application configuration."""

    origins: list[str] = field(default_factory=lambda: ["EWR", "JFK", "LGA"])
    currency: str = "USD"
    country: str = "US"
    language: str = "en-US"
    max_stops: str = "ONE_STOP_OR_FEWER"
    max_layover_hours: float = 6.0
    explore_price_ceilings: dict[str, float] = field(
        default_factory=lambda: {
            REGION_EUROPE: 420.0,
            REGION_CARIBBEAN: 260.0,
            REGION_SOUTH_AMERICA: 450.0,
            REGION_ASIA: 680.0,
        }
    )
    drop_threshold_pct: float = 35.0
    baseline_window_days: int = 90
    history_retention_days: int = 180
    min_baseline_observations: int = 3
    max_sms_alerts_per_run: int = 5
    price_bucket_size: float = 25.0
    explore_scan: ExploreScanConfig = field(default_factory=ExploreScanConfig)
    pinned_watches: list[PinnedWatch] = field(default_factory=list)
    secret_flying_enabled: bool = True
    secret_flying_keywords: list[str] = field(
        default_factory=lambda: ["EWR", "JFK", "LGA", "New York", "Newark", "NYC"]
    )

    def get_region_for_destination(
        self,
        country: Optional[str] = None,
        destination_name: Optional[str] = None,
        airport_code: Optional[str] = None,
    ) -> Optional[str]:
        """Classify a destination into one of the target explore regions."""
        c = (country or "").strip().lower()
        d = (destination_name or "").strip().lower()

        # Check country lists
        if c in EUROPE_COUNTRIES or any(k in d for k in ["london", "paris", "rome", "lisbon", "madrid", "dublin", "amsterdam", "barcelona", "berlin", "milan", "vienna"]):
            return REGION_EUROPE
        if c in CARIBBEAN_CENTRAL_AMERICA_COUNTRIES or any(k in d for k in ["cancun", "cancún", "san juan", "punta cana", "cabo", "san jose", "panama"]):
            return REGION_CARIBBEAN
        if c in SOUTH_AMERICA_COUNTRIES or any(k in d for k in ["bogota", "bogotá", "medellin", "lima", "sao paulo", "rio de janeiro", "buenos aires", "santiago"]):
            return REGION_SOUTH_AMERICA
        if c in ASIA_COUNTRIES or any(k in d for k in ["tokyo", "seoul", "taipei", "singapore", "bangkok", "osaka", "hong kong"]):
            return REGION_ASIA

        return None

    def get_ceiling_for_region(self, region: Optional[str]) -> Optional[float]:
        """Return target price ceiling for a given region."""
        if not region:
            return None
        return self.explore_price_ceilings.get(region)

    def is_international(self, country: Optional[str], airport_code: Optional[str]) -> bool:
        """Check if destination is international (or Caribbean territory like Puerto Rico/USVI)."""
        if not country:
            return True
        c = country.strip().lower()
        if c in ("united states", "usa", "us"):
            # Exclude domestic US 50 states, but allow Puerto Rico or USVI if mapped
            if airport_code in ("SJU", "BQN", "PSE", "STT", "STX"):
                return True
            return False
        return True


def load_config(config_path: str | Path = "config.yaml") -> TrackerConfig:
    """Load configuration from YAML file or return defaults."""
    path = Path(config_path)
    if not path.is_file():
        logger.warning(f"Config file not found at {path}, using default configuration.")
        return TrackerConfig()

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    origins = data.get("origins", ["EWR", "JFK", "LGA"])
    currency = data.get("currency", "USD")
    country = data.get("country", "US")
    language = data.get("language", "en-US")
    max_stops = data.get("max_stops", "ONE_STOP_OR_FEWER")
    max_layover_hours = float(data.get("max_layover_hours", 6.0))
    ceilings = data.get("explore_price_ceilings", {})
    drop_threshold = float(data.get("drop_threshold_pct", 35.0))
    baseline_window = int(data.get("baseline_window_days", 90))
    history_retention = int(data.get("history_retention_days", 180))
    min_observations = int(data.get("min_baseline_observations", 3))
    max_sms = int(data.get("max_sms_alerts_per_run", 5))
    bucket_size = float(data.get("price_bucket_size", 25.0))

    scan_data = data.get("explore_scan", {})
    explore_scan = ExploreScanConfig(
        window_weeks=scan_data.get("window_weeks", [1, 2]),
        months_ahead=int(scan_data.get("months_ahead", 6)),
        sample_interval_days=int(scan_data.get("sample_interval_days", 21)),
        max_dates_per_run=int(scan_data.get("max_dates_per_run", 2)),
    )

    pinned_watches = []
    for pw in data.get("pinned_watches", []):
        pinned_watches.append(
            PinnedWatch(
                origin=pw.get("origin", "EWR"),
                destination=pw.get("destination", ""),
                departure_date=str(pw.get("departure_date", "")),
                return_date=str(pw.get("return_date", "")),
                cabin_class=pw.get("cabin_class", "ECONOMY"),
                target_price=float(pw.get("target_price", 500.0)),
                booked_price=float(pw.get("booked_price")) if pw.get("booked_price") is not None else None,
                notes=pw.get("notes"),
            )
        )

    sf_data = data.get("secret_flying", {})
    sf_enabled = sf_data.get("enabled", True)
    sf_keywords = sf_data.get("keywords", ["EWR", "JFK", "LGA", "New York", "Newark", "NYC"])

    return TrackerConfig(
        origins=origins,
        currency=currency,
        country=country,
        language=language,
        max_stops=max_stops,
        max_layover_hours=max_layover_hours,
        explore_price_ceilings=ceilings,
        drop_threshold_pct=drop_threshold,
        baseline_window_days=baseline_window,
        history_retention_days=history_retention,
        min_baseline_observations=min_observations,
        max_sms_alerts_per_run=max_sms,
        price_bucket_size=bucket_size,
        explore_scan=explore_scan,
        pinned_watches=pinned_watches,
        secret_flying_enabled=sf_enabled,
        secret_flying_keywords=sf_keywords,
    )
