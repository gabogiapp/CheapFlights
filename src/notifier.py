"""Multi-channel push notification module supporting Telegram, Discord, and ntfy.sh."""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

from src.models import DealType, FlightDeal

logger = logging.getLogger(__name__)


class Notifier:
    """Dispatches flight deal notifications to configured push channels."""

    def __init__(
        self,
        telegram_token: str | None = None,
        telegram_chat_id: str | None = None,
        discord_webhook_url: str | None = None,
        ntfy_topic: str | None = None,
        dry_run: bool = False,
    ):
        self.telegram_token = telegram_token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id = telegram_chat_id or os.getenv("TELEGRAM_CHAT_ID")
        self.discord_webhook_url = discord_webhook_url or os.getenv("DISCORD_WEBHOOK_URL")
        self.ntfy_topic = ntfy_topic or os.getenv("NTFY_TOPIC")
        self.dry_run = dry_run or os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes")

    @property
    def has_active_channels(self) -> bool:
        """Return True if at least one notification channel is configured."""
        return bool(
            (self.telegram_token and self.telegram_chat_id)
            or self.discord_webhook_url
            or self.ntfy_topic
        )

    def send_telegram(self, deal: FlightDeal) -> bool:
        """Send formatted HTML deal alert to Telegram bot channel."""
        if not (self.telegram_token and self.telegram_chat_id):
            return False

        if deal.deal_type == DealType.REPRICE_CREDIT:
            text = (
                f"<b>{deal.headline_badge}</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"✈️ <b>Route:</b> {deal.origin} → {deal.destination}"
                f"{f' ({deal.destination_name})' if deal.destination_name else ''}\n"
                f"💰 <b>Current Price:</b> ${deal.price:,.0f} {deal.currency} round-trip\n"
                f"💳 <b>Booked Price:</b> ${deal.booked_price:,.0f} {deal.currency}\n"
                f"💵 <b>Airline eCredit:</b> <b>${deal.potential_savings:,.0f} savings!</b>\n"
                f"🛫 <b>Airline:</b> {deal.airline} ({deal.formatted_stops})\n"
                f"📅 <b>Travel Dates:</b> {deal.departure_date} to {deal.return_date}\n"
                f"⏱️ <b>Flight Duration:</b> {deal.formatted_duration}\n"
                f"💡 <i>Cancel and rebook in Main Cabin to claim your eCredit voucher.</i>\n\n"
                f"👉 <a href=\"{deal.deep_link}\">View on Google Flights</a>"
            )
        elif deal.deal_type == DealType.SECRET_FLYING:
            text = (
                f"<b>{deal.headline_badge}</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"✈️ <b>Route:</b> {deal.origin} → {deal.destination}"
                f"{f' ({deal.destination_name})' if deal.destination_name else ''}\n"
                f"💰 <b>Price:</b> ${deal.price:,.0f} {deal.currency} round-trip\n"
                f"🛫 <b>Airline:</b> {deal.airline}\n"
                f"📅 <b>Dates:</b> {deal.departure_date}\n"
                f"{f'📝 <b>Details:</b> {deal.notes}' if deal.notes else ''}\n\n"
                f"👉 <a href=\"{deal.deep_link}\">Open Secret Flying Deal</a>"
            )
        else:
            drop_str = f" ({deal.drop_pct:.0f}% below baseline)" if deal.drop_pct else ""
            baseline_str = f"\n📉 <b>Baseline:</b> ${deal.baseline_price:,.0f} {deal.currency}" if deal.baseline_price else ""
            text = (
                f"<b>{deal.headline_badge}</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"✈️ <b>Route:</b> {deal.origin} → {deal.destination}"
                f"{f' ({deal.destination_name})' if deal.destination_name else ''}\n"
                f"💰 <b>Price:</b> <b>${deal.price:,.0f} {deal.currency}</b> round-trip{drop_str}"
                f"{baseline_str}\n"
                f"🛫 <b>Airline:</b> {deal.airline} ({deal.formatted_stops})\n"
                f"📅 <b>Dates:</b> {deal.departure_date} → {deal.return_date}\n"
                f"⏱️ <b>Flight Duration:</b> {deal.formatted_duration}\n\n"
                f"👉 <a href=\"{deal.deep_link}\">One-Tap Booking on Google Flights</a>"
            )

        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        payload = {
            "chat_id": self.telegram_chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }

        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            logger.info(f"Sent Telegram alert for {deal.origin}->{deal.destination}")
            return True
        except Exception as e:
            logger.error(f"Failed to send Telegram alert: {e}")
            return False

    def send_discord(self, deal: FlightDeal) -> bool:
        """Send rich embed deal alert to Discord webhook."""
        if not self.discord_webhook_url:
            return False

        # Colors: Red for mistake fare, Gold for explore drop, Blue for reprice credit, Purple for Secret Flying
        if deal.deal_type == DealType.REPRICE_CREDIT:
            color = 0x3498DB  # Blue
            title = f"{deal.headline_badge}: {deal.origin} → {deal.destination}"
            desc = f"Fare dropped! Cancel and rebook your ticket to claim **${deal.potential_savings:,.0f} in airline eCredits**."
        elif deal.deal_type == DealType.SECRET_FLYING:
            color = 0x9B59B6  # Purple
            title = f"{deal.headline_badge}: {deal.destination_name or deal.destination}"
            desc = deal.notes or "Spotted on Secret Flying New York flash sales."
        elif deal.deal_type == DealType.MISTAKE_FARE:
            color = 0xE74C3C  # Fire Red
            title = f"{deal.headline_badge}: {deal.origin} → {deal.destination} (${deal.price:,.0f})"
            desc = f"Significant price anomaly detected out of {deal.origin}!"
        else:
            color = 0xF1C40F  # Gold/Yellow
            title = f"{deal.headline_badge}: {deal.origin} → {deal.destination} (${deal.price:,.0f})"
            desc = f"International fare drop detected out of {deal.origin}."

        fields: list[dict[str, Any]] = [
            {"name": "💰 Round-Trip Fare", "value": f"${deal.price:,.0f} {deal.currency}", "inline": True},
            {"name": "✈️ Airline & Stops", "value": f"{deal.airline}\n({deal.formatted_stops})", "inline": True},
        ]

        if deal.deal_type == DealType.REPRICE_CREDIT:
            fields.append({"name": "💳 Original Booked Fare", "value": f"${deal.booked_price:,.0f} {deal.currency}", "inline": True})
            fields.append({"name": "💵 eCredit Savings", "value": f"**${deal.potential_savings:,.0f}**", "inline": True})
        elif deal.drop_pct:
            fields.append({"name": "📉 Drop from Baseline", "value": f"{deal.drop_pct:.0f}% drop (Baseline: ${deal.baseline_price:,.0f})", "inline": True})

        fields.append({"name": "📅 Travel Dates", "value": f"{deal.departure_date} → {deal.return_date}", "inline": True})
        fields.append({"name": "⏱️ Flight Duration", "value": deal.formatted_duration, "inline": True})
        fields.append({"name": "🔗 Booking Deep Link", "value": f"[Open Google Flights Search]({deal.deep_link})", "inline": False})

        embed = {
            "title": title,
            "description": desc,
            "url": deal.deep_link,
            "color": color,
            "fields": fields,
            "footer": {"text": "EWR / NYC Serverless Flight Tracker • GitHub Actions"},
        }

        try:
            resp = requests.post(self.discord_webhook_url, json={"embeds": [embed]}, timeout=10)
            resp.raise_for_status()
            logger.info(f"Sent Discord alert for {deal.origin}->{deal.destination}")
            return True
        except Exception as e:
            logger.error(f"Failed to send Discord alert: {e}")
            return False

    def send_ntfy(self, deal: FlightDeal) -> bool:
        """Send push notification via ntfy.sh topic."""
        if not self.ntfy_topic:
            return False

        topic = self.ntfy_topic.strip().lstrip("/")
        url = f"https://ntfy.sh/{topic}"

        title = f"{deal.headline_badge}: {deal.origin} -> {deal.destination} (${deal.price:,.0f})"
        if deal.deal_type == DealType.REPRICE_CREDIT:
            body = (
                f"eCredit Alert! Price dropped to ${deal.price:,.0f} "
                f"(${deal.potential_savings:,.0f} savings vs ${deal.booked_price:,.0f} booked).\n"
                f"{deal.airline} • {deal.formatted_stops}\n"
                f"Dates: {deal.departure_date} to {deal.return_date}"
            )
            tags = "moneybag,credit_card,airplane"
        else:
            drop_info = f" ({deal.drop_pct:.0f}% drop)" if deal.drop_pct else ""
            body = (
                f"${deal.price:,.0f} {deal.currency} round-trip{drop_info}\n"
                f"{deal.airline} • {deal.formatted_stops} • {deal.formatted_duration}\n"
                f"Dates: {deal.departure_date} to {deal.return_date}"
            )
            tags = "airplane,fire,tada"

        headers = {
            "Title": title,
            "Priority": "high",
            "Tags": tags,
            "Click": deal.deep_link,
        }

        try:
            resp = requests.post(url, data=body.encode("utf-8"), headers=headers, timeout=10)
            resp.raise_for_status()
            logger.info(f"Sent ntfy.sh alert to topic '{topic}'")
            return True
        except Exception as e:
            logger.error(f"Failed to send ntfy.sh alert: {e}")
            return False

    def notify(self, deal: FlightDeal) -> dict[str, bool]:
        """Dispatch deal alert to all active channels."""
        results = {"telegram": False, "discord": False, "ntfy": False}

        if self.dry_run:
            logger.info(
                f"[DRY_RUN ALERT] {deal.headline_badge}: {deal.origin} -> {deal.destination} "
                f"(${deal.price:,.0f} {deal.currency}) | Airline: {deal.airline} | "
                f"Dates: {deal.departure_date} to {deal.return_date} | Link: {deal.deep_link}"
            )
            return {"telegram": True, "discord": True, "ntfy": True}

        if self.telegram_token and self.telegram_chat_id:
            results["telegram"] = self.send_telegram(deal)
        if self.discord_webhook_url:
            results["discord"] = self.send_discord(deal)
        if self.ntfy_topic:
            results["ntfy"] = self.send_ntfy(deal)

        return results
