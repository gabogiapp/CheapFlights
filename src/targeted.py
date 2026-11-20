"""Targeted flight trip scanner for user-defined deadlines, vacation windows, and custom routes."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional
import urllib.parse

from src.models import DealType, FlightDeal, TargetedWatch, WatchMode
from src.watches import WatchManager

logger = logging.getLogger(__name__)


def build_google_flights_url(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: Optional[str] = None,
    trip_type: str = "ROUND_TRIP",
    currency: str = "USD",
) -> str:
    """Build a Google Flights deep link that strictly respects one-way vs round-trip."""
    is_one_way = (
        trip_type.upper() in ("ONE_WAY", "ONE-WAY")
        or not return_date
        or return_date == departure_date
    )

    if is_one_way:
        query = f"One-way flights from {origin} to {destination} on {departure_date}"
    else:
        query = f"Flights from {origin} to {destination} on {departure_date} through {return_date}"

    return f"https://www.google.com/travel/flights?q={urllib.parse.quote(query)}&curr={currency}&hl=en-US&gl=US"


class TargetedTripScanner:
    """Scans and verifies custom targeted flight watches.

    Supports:
    1. ARRIVAL_DEADLINE: Flights that land on or before a specific date (e.g. FCO -> PVG landing Jan 4th).
    2. VACATION_WINDOW: Flexible window (e.g. Spring Break, March 14-21, 6-8 nights).
    3. FIXED_DATES: Exact dates with price ceiling alerts.
    """

    def __init__(
        self,
        watch_manager: Optional[WatchManager] = None,
        currency: str = "USD",
    ):
        self.watch_manager = watch_manager or WatchManager()
        self.currency = currency

    def scan_all(self) -> list[FlightDeal]:
        """Scan all active watches and return qualified flight deals."""
        active_watches = self.watch_manager.get_active()
        if not active_watches:
            logger.info("No active targeted watches configured.")
            return []

        logger.info(f"TargetedTripScanner checking {len(active_watches)} active watches...")
        qualified_deals: list[FlightDeal] = []

        for watch in active_watches:
            try:
                deals = self.scan_watch(watch)
                if deals:
                    qualified_deals.extend(deals)
            except Exception as e:
                logger.error(f"Error scanning targeted watch {watch.origin}->{watch.destination} ({watch.id}): {e}")

        # Persist updated check timestamps & lowest prices
        self.watch_manager.save()
        return qualified_deals

    def scan_watch(self, watch: TargetedWatch) -> list[FlightDeal]:
        """Scan a single targeted watch according to its mode."""
        if watch.mode == WatchMode.ARRIVAL_DEADLINE:
            return self._scan_arrival_deadline(watch)
        elif watch.mode == WatchMode.VACATION_WINDOW:
            return self._scan_vacation_window(watch)
        elif watch.mode == WatchMode.FIXED_DATES:
            return self._scan_fixed_dates(watch)
        else:
            return self._scan_unified(watch)

    def _scan_unified(self, watch: TargetedWatch) -> list[FlightDeal]:
        """Scan a unified watch with optional/wildcard fields and flexible date ranges."""
        # 1. Origins (support comma separated or default)
        raw_origins = [o.strip().upper() for o in watch.origin.split(",") if o.strip()]
        origins = raw_origins if raw_origins and raw_origins != ["ANY"] else ["EWR", "JFK"]

        # 2. Destinations (support comma separated or default)
        raw_dests = [d.strip().upper() for d in watch.destination.split(",") if d.strip()]
        if not raw_dests or raw_dests == ["ANY"]:
            dests = ["FCO", "CDG", "LHR", "BCN", "MIA", "CUN", "NRT", "PVG"]
        else:
            dests = raw_dests

        is_one_way = watch.trip_type.upper() in ("ONE_WAY", "ONE-WAY")
        deals = []
        lowest_overall = float("inf")

        # 3. Determine departure candidate dates
        dep_candidates = []
        if watch.departure_date and watch.departure_date_end:
            try:
                s_dt = datetime.strptime(watch.departure_date, "%Y-%m-%d")
                e_dt = datetime.strptime(watch.departure_date_end, "%Y-%m-%d")
                curr = s_dt
                step = max(1, (e_dt - s_dt).days // 4) if (e_dt - s_dt).days > 4 else 1
                while curr <= e_dt and len(dep_candidates) < 5:
                    dep_candidates.append(curr.strftime("%Y-%m-%d"))
                    curr += timedelta(days=step)
            except Exception:
                dep_candidates = [watch.departure_date]
        elif watch.departure_date:
            dep_candidates = [watch.departure_date]
        elif watch.must_arrive_by:
            try:
                deadline_dt = datetime.strptime(watch.must_arrive_by, "%Y-%m-%d")
                dep_candidates = [
                    (deadline_dt - timedelta(days=1)).strftime("%Y-%m-%d"),
                    deadline_dt.strftime("%Y-%m-%d"),
                ]
            except Exception:
                pass
        elif watch.window_start:
            dep_candidates = [watch.window_start]

        if not dep_candidates:
            dep_candidates = [(datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d")]

        # 4. Generate candidate search pairs (dep_date, ret_date)
        search_pairs = []
        for dep_d in dep_candidates:
            if is_one_way:
                search_pairs.append((dep_d, None))
            else:
                if watch.return_date and watch.return_date_end:
                    try:
                        s_ret = datetime.strptime(watch.return_date, "%Y-%m-%d")
                        e_ret = datetime.strptime(watch.return_date_end, "%Y-%m-%d")
                        ret_curr = s_ret
                        while ret_curr <= e_ret and len(search_pairs) < 6:
                            search_pairs.append((dep_d, ret_curr.strftime("%Y-%m-%d")))
                            ret_curr += timedelta(days=2)
                    except Exception:
                        search_pairs.append((dep_d, watch.return_date))
                elif watch.return_date:
                    search_pairs.append((dep_d, watch.return_date))
                else:
                    try:
                        dep_dt = datetime.strptime(dep_d, "%Y-%m-%d")
                        nights = (watch.min_nights + watch.max_nights) // 2 if watch.min_nights else 7
                        search_pairs.append((dep_d, (dep_dt + timedelta(days=nights)).strftime("%Y-%m-%d")))
                    except Exception:
                        pass

        if len(search_pairs) > 6:
            search_pairs = search_pairs[:6]

        # 5. Execute searches across combinations
        for orig in origins[:2]:
            for dest in dests[:3]:
                for dep_date, ret_date in search_pairs:
                    flights = self._query_fast_flights(
                        origin=orig,
                        destination=dest,
                        dep_date=dep_date,
                        ret_date=ret_date if not is_one_way else None,
                        max_stops=watch.max_stops,
                        seat=watch.cabin_class,
                    )

                    for f in flights:
                        legs = f.get("legs", [])
                        if not legs:
                            continue

                        # Check arrival deadline if specified
                        if watch.must_arrive_by:
                            outbound_arrival_leg = None
                            for leg in legs:
                                if leg.get("to") == dest:
                                    outbound_arrival_leg = leg
                                    break
                            if not outbound_arrival_leg:
                                outbound_arrival_leg = legs[-1]

                            arr_date = outbound_arrival_leg.get("arrival_date", "")
                            if arr_date > watch.must_arrive_by:
                                continue

                        price = f["price"]
                        if price < lowest_overall:
                            lowest_overall = price

                        if watch.target_price > 0 and price > watch.target_price:
                            continue

                        deep_link = build_google_flights_url(
                            origin=orig,
                            destination=dest,
                            departure_date=dep_date,
                            return_date=ret_date if not is_one_way else None,
                            trip_type=watch.trip_type,
                            currency=self.currency,
                        )

                        airline_name = ", ".join(f["airlines"]) if f["airlines"] else "Various"
                        note_parts = []
                        if watch.notes:
                            note_parts.append(watch.notes)
                        if watch.must_arrive_by:
                            note_parts.append(f"Lands on {legs[-1].get('arrival_date')} at {legs[-1].get('arrival_time')}")

                        deal = FlightDeal(
                            origin=orig,
                            destination=dest,
                            departure_date=dep_date,
                            return_date=ret_date or dep_date,
                            price=price,
                            currency=self.currency,
                            deal_type=DealType.TARGETED_TRIP,
                            destination_name=watch.destination_name or dest,
                            airline=airline_name,
                            stops=f["stops"],
                            duration_minutes=f["total_duration"],
                            deep_link=deep_link,
                            notes=" • ".join(note_parts),
                            source="targeted_unified",
                            extra_metadata={
                                "watch_id": watch.id,
                                "target_price": watch.target_price,
                                "must_arrive_by": watch.must_arrive_by,
                            },
                        )
                        deals.append(deal)

        if lowest_overall < float("inf"):
            self.watch_manager.update_result(watch.id, lowest_overall)

        deals.sort(key=lambda d: d.price)
        return deals[:3]

    def _query_fast_flights(
        self,
        origin: str,
        destination: str,
        dep_date: str,
        ret_date: Optional[str] = None,
        max_stops: int = 1,
        seat: str = "economy",
    ) -> list[dict]:
        """Execute fast-flights query and return parsed flight structures."""
        try:
            import fast_flights
            from fast_flights import FlightQuery, Passengers, create_query, get_flights

            stops_filter = None if (max_stops is None or max_stops >= 99 or max_stops < 0) else max_stops
            queries = [
                FlightQuery(
                    date=dep_date,
                    from_airport=origin,
                    to_airport=destination,
                    max_stops=stops_filter,
                )
            ]

            trip_type = "one-way"
            if ret_date:
                trip_type = "round-trip"
                queries.append(
                    FlightQuery(
                        date=ret_date,
                        from_airport=destination,
                        to_airport=origin,
                        max_stops=stops_filter,
                    )
                )

            query = create_query(
                flights=queries,
                trip=trip_type,
                seat=seat.lower(),
                passengers=Passengers(adults=1),
                currency=self.currency,
            )

            res = get_flights(query)
            flights = res.flights if hasattr(res, "flights") else res
            parsed = []

            for f in flights:
                price = getattr(f, "price", 0.0)
                if not price or float(price) <= 0:
                    continue

                airlines = getattr(f, "airlines", [])
                legs = getattr(f, "flights", [])

                parsed_legs = []
                for leg in legs:
                    dep_d = leg.departure.date
                    dep_t = leg.departure.time
                    arr_d = leg.arrival.date
                    arr_t = leg.arrival.time
                    parsed_legs.append({
                        "from": leg.from_airport.code if hasattr(leg.from_airport, "code") else str(leg.from_airport),
                        "to": leg.to_airport.code if hasattr(leg.to_airport, "code") else str(leg.to_airport),
                        "departure_date": f"{dep_d[0]:04d}-{dep_d[1]:02d}-{dep_d[2]:02d}",
                        "departure_time": f"{dep_t[0]:02d}:{dep_t[1]:02d}",
                        "arrival_date": f"{arr_d[0]:04d}-{arr_d[1]:02d}-{arr_d[2]:02d}",
                        "arrival_time": f"{arr_t[0]:02d}:{arr_t[1]:02d}",
                        "duration": getattr(leg, "duration", 0),
                        "plane": getattr(leg, "plane_type", ""),
                    })

                parsed.append({
                    "price": float(price),
                    "airlines": airlines,
                    "legs": parsed_legs,
                    "stops": max(0, len(parsed_legs) - 1) if trip_type == "one-way" else (len(parsed_legs) - 2 if len(parsed_legs) >= 2 else 0),
                    "total_duration": sum(l["duration"] for l in parsed_legs),
                })

            return parsed
        except Exception as e:
            logger.warning(f"fast-flights lookup failed for {origin}->{destination} on {dep_date}: {e}")
            return []

    def _scan_arrival_deadline(self, watch: TargetedWatch) -> list[FlightDeal]:
        """Scan flights that must arrive on or before a target deadline."""
        if not watch.must_arrive_by:
            logger.warning(f"Watch {watch.id} has ARRIVAL_DEADLINE mode but no must_arrive_by date set.")
            return []

        try:
            deadline_dt = datetime.strptime(watch.must_arrive_by, "%Y-%m-%d")
        except ValueError:
            logger.error(f"Invalid must_arrive_by date: {watch.must_arrive_by}")
            return []

        # Candidate departure dates: if departure_date is explicitly given, test it.
        # Otherwise test 1 day before (overnight long haul) and same day (short/medium haul).
        if watch.departure_date:
            candidate_dep_dates = [watch.departure_date]
        else:
            candidate_dep_dates = [
                (deadline_dt - timedelta(days=1)).strftime("%Y-%m-%d"),
                deadline_dt.strftime("%Y-%m-%d"),
            ]

        deals = []
        lowest_overall = float("inf")

        for dep_date in candidate_dep_dates:
            flights = self._query_fast_flights(
                origin=watch.origin,
                destination=watch.destination,
                dep_date=dep_date,
                ret_date=watch.return_date if watch.trip_type == "ROUND_TRIP" else None,
                max_stops=watch.max_stops,
                seat=watch.cabin_class,
            )

            for f in flights:
                legs = f["legs"]
                if not legs:
                    continue

                # Final arrival leg of the outbound journey
                # If round-trip, outbound arrives at destination; if one-way, last leg arrives at destination
                outbound_arrival_leg = None
                for leg in legs:
                    if leg["to"] == watch.destination:
                        outbound_arrival_leg = leg
                        break
                if not outbound_arrival_leg:
                    outbound_arrival_leg = legs[-1]

                arr_date = outbound_arrival_leg["arrival_date"]
                arr_time = outbound_arrival_leg["arrival_time"]

                # Strict check: landing must match must_arrive_by date
                if arr_date != watch.must_arrive_by:
                    continue

                price = f["price"]
                if price < lowest_overall:
                    lowest_overall = price

                # Check against target price threshold (0 means watch all prices)
                if watch.target_price > 0 and price > watch.target_price:
                    continue

                deep_link = build_google_flights_url(
                    origin=watch.origin,
                    destination=watch.destination,
                    departure_date=dep_date,
                    return_date=watch.return_date if watch.trip_type == "ROUND_TRIP" else None,
                    trip_type=watch.trip_type,
                    currency=self.currency,
                )

                airline_name = ", ".join(f["airlines"]) if f["airlines"] else "Various"
                arrival_note = f"Lands in {watch.destination} on {arr_date} at {arr_time}"
                notes = f"{watch.notes} • {arrival_note}" if watch.notes else arrival_note

                deal = FlightDeal(
                    origin=watch.origin,
                    destination=watch.destination,
                    departure_date=dep_date,
                    return_date=watch.return_date or dep_date,
                    price=price,
                    currency=self.currency,
                    deal_type=DealType.TARGETED_TRIP,
                    destination_name=watch.destination_name,
                    airline=airline_name,
                    stops=f["stops"],
                    duration_minutes=f["total_duration"],
                    deep_link=deep_link,
                    notes=notes,
                    source="targeted_arrival_deadline",
                    extra_metadata={
                        "watch_id": watch.id,
                        "target_price": watch.target_price,
                        "must_arrive_by": watch.must_arrive_by,
                        "arrival_info": f"{arr_date} at {arr_time}",
                        "outbound_dep": dep_date,
                    },
                )
                deals.append(deal)

        if lowest_overall < float("inf"):
            self.watch_manager.update_result(watch.id, lowest_overall)

        # Return the best (lowest price) matching flight deals for this watch
        deals.sort(key=lambda d: d.price)
        return deals[:3]

    def _scan_vacation_window(self, watch: TargetedWatch) -> list[FlightDeal]:
        """Scan flights within a flexible vacation window (e.g. Spring Break)."""
        if not watch.window_start or not watch.window_end:
            logger.warning(f"Watch {watch.id} has VACATION_WINDOW mode but missing window_start/window_end.")
            return []

        try:
            start_dt = datetime.strptime(watch.window_start, "%Y-%m-%d")
            end_dt = datetime.strptime(watch.window_end, "%Y-%m-%d")
        except ValueError:
            logger.error(f"Invalid window dates: {watch.window_start} -> {watch.window_end}")
            return []

        # Generate candidate date pairs that fit [window_start, window_end] and [min_nights, max_nights]
        candidate_pairs = []
        curr = start_dt
        while curr <= end_dt:
            for nights in range(watch.min_nights, watch.max_nights + 1):
                ret = curr + timedelta(days=nights)
                if ret <= end_dt:
                    candidate_pairs.append((curr.strftime("%Y-%m-%d"), ret.strftime("%Y-%m-%d")))
            curr += timedelta(days=1)

        # Cap checks to 6 candidate pairs to prevent rate-limiting Google Flights
        if len(candidate_pairs) > 6:
            candidate_pairs = candidate_pairs[:6]

        deals = []
        lowest_overall = float("inf")

        for dep_date, ret_date in candidate_pairs:
            flights = self._query_fast_flights(
                origin=watch.origin,
                destination=watch.destination,
                dep_date=dep_date,
                ret_date=ret_date,
                max_stops=watch.max_stops,
                seat=watch.cabin_class,
            )

            for f in flights:
                price = f["price"]
                if price < lowest_overall:
                    lowest_overall = price

                if watch.target_price > 0 and price > watch.target_price:
                    continue

                deep_link = build_google_flights_url(
                    origin=watch.origin,
                    destination=watch.destination,
                    departure_date=dep_date,
                    return_date=ret_date,
                    trip_type="ROUND_TRIP",
                    currency=self.currency,
                )

                airline_name = ", ".join(f["airlines"]) if f["airlines"] else "Various"
                notes = f"Vacation window ({dep_date} to {ret_date}, {(datetime.strptime(ret_date, '%Y-%m-%d') - datetime.strptime(dep_date, '%Y-%m-%d')).days} nights)"
                if watch.notes:
                    notes = f"{watch.notes} • {notes}"

                deal = FlightDeal(
                    origin=watch.origin,
                    destination=watch.destination,
                    departure_date=dep_date,
                    return_date=ret_date,
                    price=price,
                    currency=self.currency,
                    deal_type=DealType.TARGETED_TRIP,
                    destination_name=watch.destination_name,
                    airline=airline_name,
                    stops=f["stops"],
                    duration_minutes=f["total_duration"],
                    deep_link=deep_link,
                    notes=notes,
                    source="targeted_vacation_window",
                    extra_metadata={
                        "watch_id": watch.id,
                        "target_price": watch.target_price,
                        "window_start": watch.window_start,
                        "window_end": watch.window_end,
                    },
                )
                deals.append(deal)

        if lowest_overall < float("inf"):
            self.watch_manager.update_result(watch.id, lowest_overall)

        deals.sort(key=lambda d: d.price)
        return deals[:3]

    def _scan_fixed_dates(self, watch: TargetedWatch) -> list[FlightDeal]:
        """Scan flights for fixed departure and optional return dates."""
        if not watch.departure_date:
            logger.warning(f"Watch {watch.id} has FIXED_DATES mode but missing departure_date.")
            return []

        flights = self._query_fast_flights(
            origin=watch.origin,
            destination=watch.destination,
            dep_date=watch.departure_date,
            ret_date=watch.return_date if watch.trip_type == "ROUND_TRIP" else None,
            max_stops=watch.max_stops,
            seat=watch.cabin_class,
        )

        deals = []
        lowest_overall = float("inf")

        for f in flights:
            price = f["price"]
            if price < lowest_overall:
                lowest_overall = price

            if watch.target_price > 0 and price > watch.target_price:
                continue

            deep_link = build_google_flights_url(
                origin=watch.origin,
                destination=watch.destination,
                departure_date=watch.departure_date,
                return_date=watch.return_date if watch.trip_type == "ROUND_TRIP" else None,
                trip_type=watch.trip_type,
                currency=self.currency,
            )

            airline_name = ", ".join(f["airlines"]) if f["airlines"] else "Various"
            deal = FlightDeal(
                origin=watch.origin,
                destination=watch.destination,
                departure_date=watch.departure_date,
                return_date=watch.return_date or watch.departure_date,
                price=price,
                currency=self.currency,
                deal_type=DealType.TARGETED_TRIP,
                destination_name=watch.destination_name,
                airline=airline_name,
                stops=f["stops"],
                duration_minutes=f["total_duration"],
                deep_link=deep_link,
                notes=watch.notes or "Targeted fixed date alert",
                source="targeted_fixed_dates",
                extra_metadata={
                    "watch_id": watch.id,
                    "target_price": watch.target_price,
                },
            )
            deals.append(deal)

        if lowest_overall < float("inf"):
            self.watch_manager.update_result(watch.id, lowest_overall)

        deals.sort(key=lambda d: d.price)
        return deals[:3]
