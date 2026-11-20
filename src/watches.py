"""Targeted trip watch manager for user-defined deadlines, windows, and custom routes."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.models import TargetedWatch, WatchMode

logger = logging.getLogger(__name__)

DEFAULT_WATCHES_FILE = Path("data/watches.json")


class WatchManager:
    """Manages persistent custom trip watches in data/watches.json."""

    def __init__(self, file_path: Path | str = DEFAULT_WATCHES_FILE):
        self.file_path = Path(file_path)
        self.watches: list[TargetedWatch] = []
        self.load()

    def load(self) -> list[TargetedWatch]:
        """Load targeted watches from JSON file."""
        if not self.file_path.exists():
            self.watches = []
            return self.watches

        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)

            watches = []
            for item in raw_data:
                mode_str = item.get("mode", "UNIFIED")
                try:
                    mode = WatchMode(mode_str)
                except ValueError:
                    mode = WatchMode.UNIFIED

                watch = TargetedWatch(
                    id=item.get("id", str(uuid.uuid4())[:8]),
                    origin=item.get("origin", "").upper() or "EWR, JFK",
                    destination=item.get("destination", "").upper() or "ANY",
                    destination_name=item.get("destination_name", ""),
                    target_price=float(item.get("target_price", 0.0)),
                    mode=mode,
                    trip_type=item.get("trip_type", "ROUND_TRIP").upper(),
                    departure_date=item.get("departure_date"),
                    departure_date_end=item.get("departure_date_end"),
                    return_date=item.get("return_date"),
                    return_date_end=item.get("return_date_end"),
                    must_arrive_by=item.get("must_arrive_by"),
                    window_start=item.get("window_start"),
                    window_end=item.get("window_end"),
                    min_nights=int(item.get("min_nights", 5)),
                    max_nights=int(item.get("max_nights", 10)),
                    cabin_class=item.get("cabin_class", "ECONOMY").upper(),
                    max_stops=int(item.get("max_stops", 1)),
                    booked_price=float(item["booked_price"]) if item.get("booked_price") else None,
                    active=bool(item.get("active", True)),
                    notes=item.get("notes"),
                    created_at=item.get("created_at", datetime.now(timezone.utc).isoformat()),
                    last_checked=item.get("last_checked"),
                    lowest_price_found=float(item["lowest_price_found"]) if item.get("lowest_price_found") else None,
                )
                watches.append(watch)

            self.watches = watches
            return self.watches
        except Exception as e:
            logger.error(f"Failed to load watches from {self.file_path}: {e}")
            self.watches = []
            return self.watches

    def save(self) -> bool:
        """Persist watches to disk."""
        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            data = []
            for w in self.watches:
                d = asdict(w)
                d["mode"] = w.mode.value if hasattr(w.mode, "value") else str(w.mode)
                data.append(d)

            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            return True
        except Exception as e:
            logger.error(f"Failed to save watches to {self.file_path}: {e}")
            return False

    def get_all(self) -> list[TargetedWatch]:
        """Return all configured watches."""
        return self.watches

    def get_active(self) -> list[TargetedWatch]:
        """Return only active watches."""
        return [w for w in self.watches if w.active]

    def add(self, watch: TargetedWatch, auto_push: bool = False) -> TargetedWatch:
        """Add a new targeted watch."""
        if not watch.id:
            watch.id = str(uuid.uuid4())[:8]
        if not watch.created_at:
            watch.created_at = datetime.now(timezone.utc).isoformat()

        # Deduplicate by ID
        self.watches = [w for w in self.watches if w.id != watch.id]
        self.watches.append(watch)
        self.save()

        if auto_push:
            mode_str = watch.mode.value if hasattr(watch.mode, "value") else str(watch.mode)
            self.sync_git(f"feat(watches): add watch {watch.origin}->{watch.destination} ({mode_str})")

        return watch

    def delete(self, watch_id: str, auto_push: bool = False) -> bool:
        """Remove a watch by id."""
        initial_len = len(self.watches)
        self.watches = [w for w in self.watches if w.id != watch_id]
        if len(self.watches) != initial_len:
            self.save()
            if auto_push:
                self.sync_git(f"chore(watches): delete watch {watch_id}")
            return True
        return False

    def toggle(self, watch_id: str, auto_push: bool = False) -> Optional[bool]:
        """Toggle active state of a watch."""
        for w in self.watches:
            if w.id == watch_id:
                w.active = not w.active
                self.save()
                if auto_push:
                    status_str = "activate" if w.active else "pause"
                    self.sync_git(f"chore(watches): {status_str} watch {w.origin}->{w.destination}")
                return w.active
        return None

    def update_result(self, watch_id: str, lowest_price: float) -> None:
        """Record check timestamp and lowest price found."""
        for w in self.watches:
            if w.id == watch_id:
                w.last_checked = datetime.now(timezone.utc).isoformat()
                w.lowest_price_found = lowest_price
                self.save()
                break

    def sync_git(self, commit_message: str = "chore(watches): update targeted trip watches") -> tuple[bool, str]:
        """Commit data/watches.json and push to GitHub so GitHub Actions runs it."""
        try:
            self.save()
            cmd = f'git add data/watches.json && git commit -m "{commit_message}" && git pull --rebase --autostash origin main && git push origin main'
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
            if res.returncode == 0:
                logger.info(f"Synced watches to GitHub: {commit_message}")
                return True, "Successfully synced to GitHub."
            else:
                logger.warning(f"Git sync warning: {res.stderr or res.stdout}")
                return False, res.stderr or res.stdout
        except Exception as e:
            logger.error(f"Git sync failed: {e}")
            return False, str(e)
