"""Google Flights Explore search orchestrator using punitarani/fli."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fli.core.links import google_flights_url
from fli.models import (
    Airport,
    ExploreRegion,
    ExploreSearchFilters,
    MaxStops,
    PassengerInfo,
    TripType,
)
from fli.search.explore import SearchExplore

from src.config import TrackerConfig
from src.models import DealType, FlightDeal
from src.state import StateManager

logger = logging.getLogger(__name__)


def _get_airport_enum(code: str) -> Optional[Airport]:
    """Retrieve Airport enum safely."""
    clean = code.strip().upper()
    try:
        return getattr(Airport, clean, None) or Airport(clean)
    except Exception:
        return None


class ExploreScanner:
    """Scans Google Flights GetExploreDestinations for open-ended international flight deals."""

    def __init__(self, config: TrackerConfig, state_mgr: StateManager):
        self.config = config
        self.state_mgr = state_mgr
        self.search_client = SearchExplore()
        self.max_layover_minutes = int(config.max_layover_hours * 60)

    def generate_scan_dates(self) -> list[str]:
        """Generate sampled departure dates across the configured lookahead window."""
        today = datetime.now(timezone.utc)
        dates: list[str] = []
        months = self.config.explore_scan.months_ahead
        interval_days = self.config.explore_scan.sample_interval_days

        # Start search roughly 3 weeks out (avoid last-minute high prices)
        current = today + timedelta(days=21)
        end_date = today + timedelta(days=months * 30)

        while current <= end_date:
            dates.append(current.strftime("%Y-%m-%d"))
            current += timedelta(days=interval_days)

        return dates

    def scan_origin(self, origin_code: str) -> list[FlightDeal]:
        """Run flexible Explore search across dates and windows for a specific origin airport."""
        deals: list[FlightDeal] = []
        origin_enum = _get_airport_enum(origin_code)
        if not origin_enum:
            logger.warning(f"Skipping explore search: invalid origin {origin_code}")
            return deals

        sample_dates = self.generate_scan_dates()
        windows = self.config.explore_scan.window_weeks  # e.g. [1, 2] weeks

        logger.info(
            f"Scanning Explore for {origin_code} across {len(sample_dates)} dates "
            f"and windows {windows}..."
        )

        for dep_date in sample_dates:
            for weeks in windows:
                nights = weeks * 7
                trip_window = [4, 23, nights, nights]
                dep_dt = datetime.strptime(dep_date, "%Y-%m-%d")
                ret_date = (dep_dt + timedelta(days=nights)).strftime("%Y-%m-%d")

                filters = ExploreSearchFilters(
                    origin=origin_enum,
                    destination=ExploreRegion.ANYWHERE,
                    departure_date=dep_date,
                    trip_type=TripType.ROUND_TRIP,
                    trip_length_window=trip_window,
                    stops=MaxStops.ONE_STOP_OR_FEWER,
                    passenger_info=PassengerInfo(adults=1),
                )

                try:
                    res = self.search_client.search(
                        filters,
                        currency=self.config.currency,
                        language=self.config.language,
                        country=self.config.country,
                    )
                except Exception as e:
                    logger.debug(
                        f"Explore search refused or failed for {origin_code} {dep_date} ({nights}n): {e}"
                    )
                    continue

                if not res or not res.destinations:
                    continue

                for dest in res.destinations:
                    # Filter out destinations with missing or zero price
                    if dest.price is None or dest.price <= 0:
                        continue

                    dest_code = dest.destination_airport or dest.name
                    country = dest.country or ""

                    # Filter out domestic US flights
                    if not self.config.is_international(country, dest.destination_airport):
                        continue

                    # Filter out itineraries with more than 1 stop
                    if dest.stops is not None and dest.stops > 1:
                        continue

                    # Filter out layovers longer than max_layover_hours
                    if (
                        dest.layover_minutes is not None
                        and dest.layover_minutes > self.max_layover_minutes
                    ):
                        continue

                    # Record price into historical baseline tracker
                    self.state_mgr.record_price(
                        origin=origin_code,
                        destination=dest_code,
                        price=dest.price,
                    )

                    # Classify region and retrieve price ceiling
                    region = self.config.get_region_for_destination(
                        country=country,
                        destination_name=dest.name,
                        airport_code=dest.destination_airport,
                    )
                    ceiling = self.config.get_ceiling_for_region(region)

                    # Compute price drop percentage against rolling 14-day baseline
                    drop_pct, baseline = self.state_mgr.evaluate_price_drop(
                        origin=origin_code,
                        destination=dest_code,
                        current_price=dest.price,
                        days=14,
                    )

                    # Evaluate alert qualification
                    meets_ceiling = ceiling is not None and dest.price <= ceiling
                    meets_drop_threshold = (
                        drop_pct is not None
                        and drop_pct >= self.config.drop_threshold_pct
                    )

                    if not (meets_ceiling or meets_drop_threshold):
                        continue

                    # Detect mistake fare threshold (50%+ drop or 40% below ceiling)
                    is_mistake_fare = False
                    if drop_pct and drop_pct >= 50.0:
                        is_mistake_fare = True
                    elif ceiling and dest.price <= (ceiling * 0.65):
                        is_mistake_fare = True

                    deal_type = (
                        DealType.MISTAKE_FARE if is_mistake_fare else DealType.EXPLORE_DROP
                    )

                    deep_link = google_flights_url(
                        origin=origin_code,
                        destination=dest.destination_airport or dest.name,
                        departure_date=dest.departure_date or dep_date,
                        return_date=ret_date,
                        currency=self.config.currency,
                        language=self.config.language,
                        country=self.config.country,
                    )

                    airline_name = (
                        dest.airline_name
                        or dest.airline
                        or "Various Airlines"
                    )

                    deal = FlightDeal(
                        origin=origin_code,
                        destination=dest.destination_airport or dest.name,
                        destination_name=f"{dest.name}, {country}" if country else dest.name,
                        country=country,
                        region=region or "International",
                        departure_date=dest.departure_date or dep_date,
                        return_date=ret_date,
                        price=dest.price,
                        currency=self.config.currency,
                        deal_type=deal_type,
                        airline=airline_name,
                        stops=dest.stops or 0,
                        duration_minutes=dest.duration_minutes,
                        layover_minutes=dest.layover_minutes,
                        baseline_price=baseline,
                        drop_pct=drop_pct,
                        deep_link=deep_link,
                        source="google_flights_explore",
                    )
                    deals.append(deal)

        return deals

    def scan_all_origins(self) -> list[FlightDeal]:
        """Scan all configured origins with EWR prioritized."""
        all_deals: list[FlightDeal] = []
        for origin in self.config.origins:
            logger.info(f"--- Processing Explore search for {origin} ---")
            origin_deals = self.scan_origin(origin)
            all_deals.extend(origin_deals)
        return all_deals
