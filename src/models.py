"""Data models for flight deals, price tracking, and notifications."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class DealType(str, Enum):
    """Categorization of alerted deals."""

    EXPLORE_DROP = "EXPLORE_DROP"
    MISTAKE_FARE = "MISTAKE_FARE"
    REPRICE_CREDIT = "REPRICE_CREDIT"
    SECRET_FLYING = "SECRET_FLYING"
    TARGETED_TRIP = "TARGETED_TRIP"


@dataclass
class FlightDeal:
    """Represents a discovered flight deal or price drop."""

    origin: str
    destination: str
    departure_date: str
    return_date: str
    price: float
    currency: str = "USD"
    deal_type: DealType = DealType.EXPLORE_DROP
    destination_name: str = ""
    country: str = ""
    region: str = ""
    airline: str = "Unknown Airline"
    stops: int = 0
    duration_minutes: Optional[int] = None
    layover_minutes: Optional[int] = None
    baseline_price: Optional[float] = None
    booked_price: Optional[float] = None
    potential_savings: Optional[float] = None
    drop_pct: Optional[float] = None
    deep_link: str = ""
    notes: Optional[str] = None
    source: str = "google_flights"
    extra_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def formatted_duration(self) -> str:
        """Format duration in minutes to human readable string (e.g. '7h 25m')."""
        if not self.duration_minutes:
            return "N/A"
        hours = self.duration_minutes // 60
        minutes = self.duration_minutes % 60
        if hours > 0 and minutes > 0:
            return f"{hours}h {minutes:02d}m"
        elif hours > 0:
            return f"{hours}h"
        return f"{minutes}m"

    @property
    def formatted_stops(self) -> str:
        """Format stop count into friendly label."""
        if self.stops == 0:
            return "Nonstop"
        elif self.stops == 1:
            if self.layover_minutes:
                hours = self.layover_minutes // 60
                mins = self.layover_minutes % 60
                layover_str = f" ({hours}h {mins:02d}m layover)" if hours else f" ({mins}m layover)"
                return f"1 stop{layover_str}"
            return "1 stop"
        return f"{self.stops} stops"

    @property
    def headline_badge(self) -> str:
        """Return headline badge for notifications."""
        if self.deal_type == DealType.TARGETED_TRIP:
            return "🎯 TARGET TRIP ALERT"
        elif self.deal_type == DealType.REPRICE_CREDIT:
            return "💳 POST-BOOKING REPRICE CREDIT ALERT"
        elif self.deal_type == DealType.MISTAKE_FARE:
            return "🔥 MISTAKE FARE / FLASH DROP"
        elif self.deal_type == DealType.SECRET_FLYING:
            return "🚨 SECRET FLYING DEAL / MISTAKE FARE"
        return "🔥 EXPLORE DROP ALERT"

    def deterministic_key(self, bucket_size: float = 25.0) -> str:
        """Generate deterministic deduplication key:

        origin-destination-dep_date-ret_date-price_bucket
        """
        bucket = int(self.price // bucket_size) * int(bucket_size)
        return f"{self.origin}-{self.destination}-{self.departure_date}-{self.return_date}-b{bucket}"


class WatchMode(str, Enum):
    """Modes for custom user-configured trip watches."""

    UNIFIED = "UNIFIED"                    # Unified flexible watch (wildcards, windows, optional fields)
    ARRIVAL_DEADLINE = "ARRIVAL_DEADLINE"  # Must land on or before a specific date (e.g. land in China Jan 4th)
    VACATION_WINDOW = "VACATION_WINDOW"    # Flexible vacation window (e.g. Spring Break March 14–21)
    FIXED_DATES = "FIXED_DATES"            # Exact departure/return or booked flight


@dataclass
class TargetedWatch:
    """Configured flight watch for specific destinations, deadlines, or vacation windows."""

    id: str
    origin: str = "EWR, JFK"
    destination: str = "ANY"
    target_price: float = 500.0
    mode: WatchMode = WatchMode.UNIFIED
    trip_type: str = "ROUND_TRIP"  # "ONE_WAY" or "ROUND_TRIP"
    destination_name: str = ""
    departure_date: Optional[str] = None       # Earliest departure or exact date
    departure_date_end: Optional[str] = None   # Latest departure (optional window)
    return_date: Optional[str] = None          # Earliest return or exact return
    return_date_end: Optional[str] = None      # Latest return (optional window)
    must_arrive_by: Optional[str] = None       # e.g. "2027-01-04"
    window_start: Optional[str] = None         # Legacy/compat window
    window_end: Optional[str] = None           # Legacy/compat window
    min_nights: int = 5
    max_nights: int = 10
    cabin_class: str = "ECONOMY"
    max_stops: int = 1
    booked_price: Optional[float] = None
    active: bool = True
    notes: Optional[str] = None
    created_at: str = ""
    last_checked: Optional[str] = None
    lowest_price_found: Optional[float] = None


@dataclass
class PinnedWatch:
    """Configured flight watch for already-booked or specific tracked itineraries."""

    origin: str
    destination: str
    departure_date: str
    return_date: str
    cabin_class: str = "ECONOMY"
    target_price: float = 500.0
    booked_price: Optional[float] = None
    notes: Optional[str] = None
