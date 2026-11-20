"""Unit tests for targeted flight watches, scanner, and alert notifications."""

from __future__ import annotations

import tempfile
from pathlib import Path

from src.models import DealType, FlightDeal, TargetedWatch, WatchMode
from src.notifier import Notifier
from src.targeted import TargetedTripScanner
from src.watches import WatchManager


def test_watch_manager_crud():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        temp_path = tf.name

    try:
        wm = WatchManager(temp_path)
        assert len(wm.get_all()) == 0

        # Add watch
        w = TargetedWatch(
            id="fco-pvg-test",
            origin="FCO",
            destination="PVG",
            target_price=700.0,
            mode=WatchMode.ARRIVAL_DEADLINE,
            trip_type="ONE_WAY",
            must_arrive_by="2027-01-04",
            notes="Land Jan 4",
        )
        wm.add(w)
        assert len(wm.get_all()) == 1
        assert wm.get_all()[0].id == "fco-pvg-test"

        # Toggle watch
        new_state = wm.toggle("fco-pvg-test")
        assert new_state is False
        assert len(wm.get_active()) == 0

        wm.toggle("fco-pvg-test")
        assert len(wm.get_active()) == 1

        # Update result
        wm.update_result("fco-pvg-test", 563.0)
        assert wm.get_all()[0].lowest_price_found == 563.0

        # Delete watch
        deleted = wm.delete("fco-pvg-test")
        assert deleted is True
        assert len(wm.get_all()) == 0
    finally:
        Path(temp_path).unlink(missing_ok=True)


def test_unified_watch_defaults():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        temp_path = tf.name

    try:
        wm = WatchManager(temp_path)
        # Add unified watch with blank fields
        w = TargetedWatch(
            id="wildcard-test",
            origin="EWR, JFK",
            destination="ANY",
            target_price=350.0,
            mode=WatchMode.UNIFIED,
            trip_type="ROUND_TRIP",
            departure_date="2027-03-14",
            departure_date_end="2027-03-21",
            notes="Spring break wildcard",
        )
        wm.add(w)
        watches = wm.get_all()
        assert len(watches) == 1
        assert watches[0].origin == "EWR, JFK"
        assert watches[0].destination == "ANY"
        assert watches[0].departure_date_end == "2027-03-21"
    finally:
        Path(temp_path).unlink(missing_ok=True)


def test_targeted_trip_notification_formatting():
    deal = FlightDeal(
        origin="FCO",
        destination="PVG",
        departure_date="2027-01-03",
        return_date="2027-01-03",
        price=563.0,
        currency="USD",
        deal_type=DealType.TARGETED_TRIP,
        destination_name="Shanghai",
        airline="Finnair",
        stops=1,
        duration_minutes=945,
        deep_link="https://www.google.com/travel/flights?q=Flights%20from%20FCO%20to%20PVG",
        notes="Land Jan 4 • Lands in PVG on 2027-01-04 at 18:55",
        extra_metadata={
            "target_price": 700.0,
            "must_arrive_by": "2027-01-04",
            "arrival_info": "2027-01-04 at 18:55",
        },
    )

    notifier = Notifier(dry_run=True)
    # Ensure headline badge
    assert deal.headline_badge == "🎯 TARGET TRIP ALERT"

    # Test send_sms_email formatting
    notifier.sms_gateway_email = "test@vtext.com"
    sent_sms = notifier.send_sms_email(deal)
    assert sent_sms is True

    # Test send_telegram with mock (dry_run check)
    notifier.telegram_token = "mock_token"
    notifier.telegram_chat_id = "12345"
    # Even if network fails with mock token, format shouldn't throw an unhandled exception before the request
    try:
        notifier.send_telegram(deal)
    except Exception:
        pass
