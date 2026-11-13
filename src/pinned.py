"""Pinned route watcher for reprice alerts and airline eCredit savings."""

from __future__ import annotations

import logging
from typing import Optional

from fli.core.links import google_flights_url
from fli.models import Airport, MaxStops, PassengerInfo, SeatType, TripType
from fli.models.google_flights.flights import FlightSegment
from fli.search.flights import FlightSearchFilters, SearchFlights

from src.models import DealType, FlightDeal, PinnedWatch

logger = logging.getLogger(__name__)

SEAT_TYPE_MAP = {
    "ECONOMY": SeatType.ECONOMY,
    "PREMIUM_ECONOMY": SeatType.PREMIUM_ECONOMY,
    "BUSINESS": SeatType.BUSINESS,
    "FIRST": SeatType.FIRST,
}


def _get_airport_enum(code: str) -> Optional[Airport]:
    """Retrieve Airport enum by code safely."""
    code_clean = code.strip().upper()
    try:
        return getattr(Airport, code_clean, None) or Airport(code_clean)
    except Exception:
        return None


class PinnedWatcher:
    """Monitors pinned booked routes for price drops eligible for rebooking credits."""

    def __init__(
        self,
        watches: list[PinnedWatch],
        currency: str = "USD",
        max_layover_hours: float = 6.0,
    ):
        self.watches = watches
        self.currency = currency
        self.max_layover_minutes = int(max_layover_hours * 60)
        self.search_client = SearchFlights()

    def search_via_fli(self, watch: PinnedWatch) -> Optional[FlightDeal]:
        """Search pinned route using punitarani/fli SearchFlights."""
        origin_enum = _get_airport_enum(watch.origin)
        dest_enum = _get_airport_enum(watch.destination)

        if not origin_enum or not dest_enum:
            logger.warning(
                f"Unknown airport code in pinned watch: {watch.origin} or {watch.destination}"
            )
            return None

        seat_type = SEAT_TYPE_MAP.get(watch.cabin_class.upper(), SeatType.ECONOMY)

        segments = [
            FlightSegment(
                departure_airport=[(origin_enum, 0)],
                arrival_airport=[(dest_enum, 0)],
                travel_date=watch.departure_date,
            ),
            FlightSegment(
                departure_airport=[(dest_enum, 0)],
                arrival_airport=[(origin_enum, 0)],
                travel_date=watch.return_date,
            ),
        ]

        filters = FlightSearchFilters(
            trip_type=TripType.ROUND_TRIP,
            passenger_info=PassengerInfo(adults=1),
            flight_segments=segments,
            stops=MaxStops.ONE_STOP_OR_FEWER,
            seat_type=seat_type,
        )

        try:
            results = self.search_client.search(filters, currency=self.currency)
            if not results:
                return None

            best_deal: Optional[FlightDeal] = None
            lowest_price = float("inf")

            for item in results:
                # Round-trip results come as (outbound, return) tuple
                if isinstance(item, tuple) and len(item) > 0:
                    outbound = item[0]
                    price = outbound.price
                    airline = getattr(outbound, "primary_airline_name", None) or (
                        outbound.primary_airline.name if hasattr(outbound, "primary_airline") and outbound.primary_airline else "Various"
                    )
                    stops = outbound.stops
                    duration = outbound.duration

                    # Check layover restrictions
                    if outbound.layovers:
                        longest_layover = max((l.duration for l in outbound.layovers if hasattr(l, "duration")), default=0)
                        if longest_layover > self.max_layover_minutes:
                            continue
                elif hasattr(item, "price"):
                    price = item.price
                    airline = getattr(item, "primary_airline_name", "Various")
                    stops = getattr(item, "stops", 0)
                    duration = getattr(item, "duration", None)
                else:
                    continue

                if price and price < lowest_price:
                    lowest_price = price
                    deep_link = google_flights_url(
                        watch.origin,
                        watch.destination,
                        watch.departure_date,
                        watch.return_date,
                        currency=self.currency,
                    )
                    best_deal = FlightDeal(
                        origin=watch.origin,
                        destination=watch.destination,
                        departure_date=watch.departure_date,
                        return_date=watch.return_date,
                        price=lowest_price,
                        currency=self.currency,
                        deal_type=DealType.REPRICE_CREDIT,
                        airline=str(airline),
                        stops=stops,
                        duration_minutes=duration,
                        booked_price=watch.booked_price,
                        potential_savings=(watch.booked_price - lowest_price) if watch.booked_price and lowest_price < watch.booked_price else None,
                        deep_link=deep_link,
                        notes=watch.notes,
                        source="fli_search_flights",
                    )

            return best_deal

        except Exception as e:
            logger.warning(f"Error querying fli SearchFlights for {watch.origin}->{watch.destination}: {e}")
            return None

    def search_via_fast_flights_fallback(self, watch: PinnedWatch) -> Optional[FlightDeal]:
        """Fallback to AWeirdDev/fast-flights if fli encounters schema errors."""
        try:
            import fast_flights
            from fast_flights import FlightQuery, Passengers, create_query, get_flights

            q1 = FlightQuery(
                date=watch.departure_date,
                from_airport=watch.origin,
                to_airport=watch.destination,
                max_stops=1,
            )
            q2 = FlightQuery(
                date=watch.return_date,
                from_airport=watch.destination,
                to_airport=watch.origin,
                max_stops=1,
            )

            query = create_query(
                flights=[q1, q2],
                trip="round-trip",
                seat="economy",
                passengers=Passengers(adults=1),
                currency=self.currency,
            )
            res = get_flights(query)
            if not res or not hasattr(res, "flights") or not res.flights:
                return None

            best_flight = min(res.flights, key=lambda f: getattr(f, "price", float("inf")))
            price = float(getattr(best_flight, "price", 0.0))
            if price <= 0:
                return None

            airlines = getattr(best_flight, "airlines", ["Various"])
            airline_str = ", ".join(airlines) if isinstance(airlines, list) else str(airlines)

            deep_link = google_flights_url(
                watch.origin,
                watch.destination,
                watch.departure_date,
                watch.return_date,
                currency=self.currency,
            )

            return FlightDeal(
                origin=watch.origin,
                destination=watch.destination,
                departure_date=watch.departure_date,
                return_date=watch.return_date,
                price=price,
                currency=self.currency,
                deal_type=DealType.REPRICE_CREDIT,
                airline=airline_str,
                stops=1,
                booked_price=watch.booked_price,
                potential_savings=(watch.booked_price - price) if watch.booked_price and price < watch.booked_price else None,
                deep_link=deep_link,
                notes=watch.notes,
                source="fast_flights_fallback",
            )
        except Exception as e:
            logger.warning(f"fast-flights fallback also failed for {watch.origin}->{watch.destination}: {e}")
            return None

    def check_all(self) -> list[FlightDeal]:
        """Check all configured pinned watches and return qualifying drop deals."""
        qualifying_deals: list[FlightDeal] = []

        for watch in self.watches:
            logger.info(
                f"Checking pinned watch {watch.origin} -> {watch.destination} "
                f"({watch.departure_date} to {watch.return_date}, target: ${watch.target_price})"
            )

            # Try primary (fli)
            deal = self.search_via_fli(watch)

            # Try fallback (fast-flights) if primary failed
            if not deal:
                logger.info(f"Trying fast-flights fallback for {watch.origin} -> {watch.destination}")
                deal = self.search_via_fast_flights_fallback(watch)

            if not deal:
                continue

            # Check qualification criteria:
            # 1. Price is below target_price
            # 2. Or price is below already-booked price (eligible for eCredit rebooking)
            is_target_met = deal.price <= watch.target_price
            has_reprice_credit = watch.booked_price and deal.price < watch.booked_price

            if is_target_met or has_reprice_credit:
                if has_reprice_credit:
                    savings = watch.booked_price - deal.price
                    deal.potential_savings = savings
                    deal.notes = (
                        f"Price dropped from ${watch.booked_price:.0f} to ${deal.price:.0f}! "
                        f"Cancel and rebook for ${savings:.0f} airline eCredit."
                    )
                logger.info(
                    f"Pinned deal matched! {watch.origin} -> {watch.destination} @ ${deal.price:.0f} "
                    f"(Target: ${watch.target_price:.0f}, Booked: ${watch.booked_price or 0:.0f})"
                )
                qualifying_deals.append(deal)
            else:
                logger.info(
                    f"Pinned route {watch.origin} -> {watch.destination} current price ${deal.price:.0f} "
                    f"does not beat target (${watch.target_price:.0f}) or booked (${watch.booked_price or 0:.0f})."
                )

        return qualifying_deals
