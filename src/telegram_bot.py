"""Interactive Telegram Bot listener for managing flight watches on the go."""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Optional

import requests
from dotenv import load_dotenv

from src.models import TargetedWatch, WatchMode
from src.watches import WatchManager

load_dotenv()
logger = logging.getLogger(__name__)


class TelegramWatchBot:
    """Listens for user commands via Telegram Bot getUpdates API."""

    def __init__(self, watch_manager: Optional[WatchManager] = None):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
        self.allowed_chat_id = str(os.getenv("TELEGRAM_CHAT_ID", "")).strip()
        self.watch_manager = watch_manager or WatchManager()
        self.running = False
        self.thread: Optional[threading.Thread] = None

    def start_in_background(self) -> None:
        """Start polling in a daemon background thread."""
        if not self.token or not self.allowed_chat_id:
            logger.warning("Telegram bot token or chat ID not set. Interactive bot listener disabled.")
            return

        self.running = True
        self.thread = threading.Thread(target=self._poll_loop, daemon=True)
        self.thread.start()
        logger.info("Telegram interactive bot polling started in background.")

    def stop(self) -> None:
        """Stop polling thread."""
        self.running = False

    def _send_reply(self, chat_id: str, text: str) -> None:
        """Send message back to user."""
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            requests.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": False,
                },
                timeout=10,
            )
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")

    def _poll_loop(self) -> None:
        """Continuous long-polling loop with offset tracking."""
        offset = 0
        base_url = f"https://api.telegram.org/bot{self.token}"

        while self.running:
            try:
                resp = requests.get(
                    f"{base_url}/getUpdates",
                    params={"offset": offset, "timeout": 20},
                    timeout=25,
                )
                if resp.status_code != 200:
                    time.sleep(5)
                    continue

                data = resp.json()
                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    msg = update.get("message")
                    if not msg:
                        continue

                    chat_id = str(msg.get("chat", {}).get("id"))
                    # Security check: only respond to the authorized chat ID
                    if self.allowed_chat_id and chat_id != self.allowed_chat_id:
                        logger.warning(f"Ignored unauthorized message from chat_id {chat_id}")
                        continue

                    text = msg.get("text", "").strip()
                    if not text:
                        continue

                    self._handle_command(chat_id, text)

            except Exception as e:
                logger.debug(f"Telegram polling exception: {e}")
                time.sleep(3)

    def _handle_command(self, chat_id: str, text: str) -> None:
        """Parse and execute commands from user."""
        parts = text.split()
        cmd = parts[0].lower().replace("@flights_tracker_bot", "")

        if cmd in ("/start", "/help"):
            help_msg = (
                "✈️ <b>Flight Watcher Commands</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "• <code>/watches</code> — List your active flight watches\n"
                "• <code>/watch &lt;from&gt; &lt;to&gt; &lt;target$&gt; &lt;date&gt;</code> — Add arrival deadline watch\n"
                "  <i>Example:</i> <code>/watch FCO PVG 700 2027-01-04</code>\n"
                "• <code>/window &lt;from&gt; &lt;to&gt; &lt;target$&gt; &lt;start&gt; &lt;end&gt;</code> — Add vacation window\n"
                "  <i>Example:</i> <code>/window JFK MIA 350 2027-03-14 2027-03-21</code>\n"
                "• <code>/del &lt;id&gt;</code> — Delete a watch\n"
                "• <code>/toggle &lt;id&gt;</code> — Pause or resume a watch\n"
                "• <code>/check</code> — Run an immediate live price scan\n"
                "• <code>/sync</code> — Push watches to GitHub Actions\n"
            )
            self._send_reply(chat_id, help_msg)

        elif cmd == "/watches":
            watches = self.watch_manager.get_all()
            if not watches:
                self._send_reply(chat_id, "📭 No flight watches configured yet. Use <code>/watch FCO PVG 700 2027-01-04</code> to add one!")
                return

            lines = ["📋 <b>Configured Flight Watches</b>\n━━━━━━━━━━━━━━━━━━━━"]
            for w in watches:
                status_icon = "🟢" if w.active else "⏸️"
                lowest_str = f" | Best: ${w.lowest_price_found:,.0f}" if w.lowest_price_found else ""
                if w.mode == WatchMode.ARRIVAL_DEADLINE:
                    criteria = f"Must land by {w.must_arrive_by}"
                elif w.mode == WatchMode.VACATION_WINDOW:
                    criteria = f"Window {w.window_start} → {w.window_end}"
                else:
                    criteria = f"Dates {w.departure_date} → {w.return_date}"

                lines.append(
                    f"{status_icon} <b>{w.origin} → {w.destination}</b> (<code>{w.id}</code>)\n"
                    f"   Target: &lt;${w.target_price:,.0f}{lowest_str}\n"
                    f"   Criteria: {criteria}\n"
                )
            self._send_reply(chat_id, "\n".join(lines))

        elif cmd == "/watch":
            # Syntax: /watch <origin> <dest> <target_price> <must_arrive_by> [notes...]
            if len(parts) < 5:
                self._send_reply(chat_id, "⚠️ Usage: <code>/watch &lt;from&gt; &lt;to&gt; &lt;max_price&gt; &lt;arrival_date_YYYY-MM-DD&gt; [notes]</code>\nExample: <code>/watch FCO PVG 700 2027-01-04 Land in Shanghai</code>")
                return

            origin = parts[1].upper()
            dest = parts[2].upper()
            try:
                target_price = float(parts[3].replace("$", ""))
            except ValueError:
                self._send_reply(chat_id, "⚠️ Invalid target price. Example: <code>700</code>")
                return

            arrive_date = parts[4]
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", arrive_date):
                self._send_reply(chat_id, "⚠️ Invalid date format. Please use <code>YYYY-MM-DD</code> (e.g. 2027-01-04).")
                return

            notes = " ".join(parts[5:]) if len(parts) > 5 else "Added via Telegram"

            new_watch = TargetedWatch(
                id=f"{origin.lower()}-{dest.lower()}-{arrive_date.replace('-', '')[-4:]}",
                origin=origin,
                destination=dest,
                target_price=target_price,
                mode=WatchMode.ARRIVAL_DEADLINE,
                trip_type="ONE_WAY",
                must_arrive_by=arrive_date,
                notes=notes,
            )

            self.watch_manager.add(new_watch, auto_push=True)
            self._send_reply(
                chat_id,
                f"✅ <b>Watch Added & Synced to GitHub!</b>\n"
                f"Route: <b>{origin} → {dest}</b>\n"
                f"Deadline: Must land by <b>{arrive_date}</b>\n"
                f"Target Price: <b>&lt;${target_price:,.0f}</b>\n"
                f"Watch ID: <code>{new_watch.id}</code>"
            )

        elif cmd == "/window":
            # Syntax: /window <origin> <dest> <target_price> <start> <end> [notes...]
            if len(parts) < 6:
                self._send_reply(chat_id, "⚠️ Usage: <code>/window &lt;from&gt; &lt;to&gt; &lt;max_price&gt; &lt;start_date&gt; &lt;end_date&gt;</code>\nExample: <code>/window JFK MIA 350 2027-03-14 2027-03-21 Spring Break</code>")
                return

            origin = parts[1].upper()
            dest = parts[2].upper()
            try:
                target_price = float(parts[3].replace("$", ""))
            except ValueError:
                self._send_reply(chat_id, "⚠️ Invalid target price.")
                return

            start_d, end_d = parts[4], parts[5]
            notes = " ".join(parts[6:]) if len(parts) > 6 else "Vacation window added via Telegram"

            new_watch = TargetedWatch(
                id=f"{origin.lower()}-{dest.lower()}-win",
                origin=origin,
                destination=dest,
                target_price=target_price,
                mode=WatchMode.VACATION_WINDOW,
                trip_type="ROUND_TRIP",
                window_start=start_d,
                window_end=end_d,
                notes=notes,
            )
            self.watch_manager.add(new_watch, auto_push=True)
            self._send_reply(
                chat_id,
                f"✅ <b>Vacation Watch Added!</b>\n"
                f"Route: <b>{origin} → {dest}</b> (Round-trip)\n"
                f"Window: <b>{start_d} → {end_d}</b>\n"
                f"Target: <b>&lt;${target_price:,.0f}</b>\n"
                f"Watch ID: <code>{new_watch.id}</code>"
            )

        elif cmd == "/del":
            if len(parts) < 2:
                self._send_reply(chat_id, "⚠️ Usage: <code>/del &lt;watch_id&gt;</code>")
                return
            watch_id = parts[1].strip()
            if self.watch_manager.delete(watch_id, auto_push=True):
                self._send_reply(chat_id, f"🗑️ Deleted watch <code>{watch_id}</code> and synced with GitHub.")
            else:
                self._send_reply(chat_id, f"⚠️ Watch ID <code>{watch_id}</code> not found.")

        elif cmd == "/toggle":
            if len(parts) < 2:
                self._send_reply(chat_id, "⚠️ Usage: <code>/toggle &lt;watch_id&gt;</code>")
                return
            watch_id = parts[1].strip()
            new_state = self.watch_manager.toggle(watch_id, auto_push=True)
            if new_state is not None:
                state_str = "🟢 Active" if new_state else "⏸️ Paused"
                self._send_reply(chat_id, f"Watch <code>{watch_id}</code> is now {state_str}.")
            else:
                self._send_reply(chat_id, f"⚠️ Watch ID <code>{watch_id}</code> not found.")

        elif cmd == "/sync":
            ok, msg = self.watch_manager.sync_git("chore(watches): sync from Telegram")
            if ok:
                self._send_reply(chat_id, "✅ Successfully synced watches with GitHub repository!")
            else:
                self._send_reply(chat_id, f"⚠️ Git sync error: {msg}")

        elif cmd == "/check":
            self._send_reply(chat_id, "🔍 Running live Google Flights check on your active watches...")
            from src.targeted import TargetedTripScanner
            scanner = TargetedTripScanner(watch_manager=self.watch_manager)
            deals = scanner.scan_all()
            if not deals:
                self._send_reply(chat_id, "ℹ️ Check completed: No prices currently under your target threshold.")
            else:
                for d in deals:
                    from src.notifier import Notifier
                    Notifier().send_telegram(d)
