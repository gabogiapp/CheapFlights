"""Flight deal live verification module to eliminate phantom fares and one-way pricing errors."""

from __future__ import annotations

import logging
from typing import Optional

from fli.models import Airport, MaxStops, PassengerInfo, SeatType, TripType
from fli.models.google_flights.flights import FlightSegment
from fli.search.flights import FlightSearchFilters, SearchFlights

from src.config import TrackerConfig
from src.models import DealType, FlightDeal

logger = logging.getLogger(__name__)


def _get_airport_enum(code: str) -> Optional[Airport]:
    """Retrieve Airport enum safely."""
    clean = code.strip().upper()
    try:
        return getattr(Airport, clean, None) or Airport(clean)
    except Exception:
        return None


class FlightVerifier:
    """Verifies that discovered deals actually exist in live airline inventory before alerting."""

    def __init__(self, config: TrackerConfig):
        self.config = config
        self.search_client = SearchFlights()

    def verify_deal(self, deal: FlightDeal) -> Optional[FlightDeal]:
        """Verify deal against live Google Flights inventory.

        Returns updated FlightDeal with live verified price and airline if confirmed,
        or None if deal is a phantom fare, one-way misquote, or above budget ceiling.
        """
        # Reprice credits on pinned routes are already queried against live inventory
        if deal.deal_type == DealType.REPRICE_CREDIT:
            return deal

        # Secret Flying posts with flexible dates bypass round-trip date locking
        if deal.return_date == "Flexible" or not deal.return_date or len(deal.departure_date) != 10:
            logger.info(f"[VERIFY] Passing through Secret Flying / flexible deal: {deal.destination_name}")
            return deal

        origin_enum = _get_airport_enum(deal.origin)
        dest_enum = _get_airport_enum(deal.destination)

        # If airport code isn't recognized by enum (e.g. city code), try fast_flights fallback
        if not origin_enum or not dest_enum:
            return self._verify_via_fast_flights(deal)

        segments = [
            FlightSegment(
                departure_airport=[(origin_enum, 0)],
                arrival_airport=[(dest_enum, 0)],
                travel_date=deal.departure_date,
            ),
            FlightSegment(
                departure_airport=[(dest_enum, 0)],
                arrival_airport=[(origin_enum, 0)],
                travel_date=deal.return_date,
            ),
        ]

        filters = FlightSearchFilters(
            trip_type=TripType.ROUND_TRIP,
            passenger_info=PassengerInfo(adults=1),
            flight_segments=segments,
            stops=MaxStops.ONE_STOP_OR_FEWER,
            seat_type=SeatType.ECONOMY,
        )

        try:
            results = self.search_client.search(
                filters,
                top_n=1,
                currency=deal.currency or self.config.currency,
                language=self.config.language,
                country=self.config.country,
            )
            if not results or not results[0]:
                logger.warning(
                    f"[VERIFY FAILED] No live round-trip flights found for {deal.origin} -> {deal.destination}"
                )
                return None

            combo = results[0]
            outbound = combo[0]
            live_price = float(outbound.price)
            live_airline = getattr(outbound, "primary_airline_name", None) or deal.airline

            # Classify region and ceiling for live price validation
            region = deal.region or self.config.get_region_for_destination(
                country=deal.country,
                destination_name=deal.destination_name,
                airport_code=deal.destination,
            )
            ceiling = self.config.get_ceiling_for_region(region)

            # Check if live price exceeds user's ceiling
            if ceiling is not None and live_price > ceiling:
                logger.warning(
                    f"[VERIFY REJECTED] {deal.origin} -> {deal.destination}: "
                    f"Explore claimed ${deal.price:,.0f}, but live round-trip is ${live_price:,.0f} "
                    f"(exceeds ${ceiling:,.0f} ceiling for {region})."
                )
                return None

            # Check if live price drastically exceeds claimed price (phantom or one-way misquote)
            if live_price > (deal.price * 1.30):
                logger.warning(
                    f"[VERIFY REJECTED] {deal.origin} -> {deal.destination}: "
                    f"Claimed ${deal.price:,.0f}, but live round-trip is ${live_price:,.0f} (phantom/one-way discrepancy)."
                )
                return None

            logger.info(
                f"[VERIFIED] {deal.origin} -> {deal.destination}: "
                f"Confirmed live round-trip at ${live_price:,.0f} on {live_airline}!"
            )
            deal.price = live_price
            deal.airline = live_airline
            return deal

        except Exception as e:
            logger.debug(
                f"SearchFlights error during verify for {deal.origin}->{deal.destination}: {e}, trying fallback"
            )
            return self._verify_via_fast_flights(deal)

    def _verify_via_fast_flights(self, deal: FlightDeal) -> Optional[FlightDeal]:
        """Fallback verifier using fast-flights."""
        try:
            from fast_flights import FlightQuery, Passengers, create_query, get_flights

            q1 = FlightQuery(
                date=deal.departure_date,
                from_airport=deal.origin,
                to_airport=deal.destination,
                max_stops=1,
            )
            q2 = FlightQuery(
                date=deal.return_date,
                from_airport=deal.destination,
                to_airport=deal.origin,
                max_stops=1,
            )

            query = create_query(
                flights=[q1, q2],
                trip="round-trip",
                seat="economy",
                passengers=Passengers(adults=1),
                currency=deal.currency or self.config.currency,
            )
            res = get_flights(query)
            flights = res.flights if hasattr(res, "flights") else res
            if not flights:
                return None

            best_flight = min(flights, key=lambda f: getattr(f, "price", float("inf")))
            live_price = float(getattr(best_flight, "price", 0.0))
            if live_price <= 0:
                return None

            region = deal.region or self.config.get_region_for_destination(
                country=deal.country,
                destination_name=deal.destination_name,
                airport_code=deal.destination,
            )
            ceiling = self.config.get_ceiling_for_region(region)

            if ceiling is not None and live_price > ceiling:
                logger.warning(
                    f"[VERIFY REJECTED via fallback] {deal.origin} -> {deal.destination}: "
                    f"Live price ${live_price:,.0f} exceeds ceiling ${ceiling:,.0f}"
                )
                return None

            if live_price > (deal.price * 1.30):
                logger.warning(
                    f"[VERIFY REJECTED via fallback] {deal.origin} -> {deal.destination}: "
                    f"Claimed ${deal.price:,.0f}, live is ${live_price:,.0f}"
                )
                return None

            airlines = getattr(best_flight, "airlines", [deal.airline])
            airline_str = ", ".join(airlines) if isinstance(airlines, list) else str(airlines)
            deal.price = live_price
            deal.airline = airline_str
            logger.info(f"[VERIFIED via fallback] {deal.origin} -> {deal.destination} at ${live_price:,.0f}")
            return deal
        except Exception as ex:
            logger.warning(f"Fallback verifier failed for {deal.origin}->{deal.destination}: {ex}")
            return None
