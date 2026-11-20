"""Zero-dependency HTTP server providing the local Flight Watcher web dashboard and REST API."""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

from src.models import TargetedWatch, WatchMode
from src.targeted import TargetedTripScanner
from src.watches import WatchManager

logger = logging.getLogger("webserver")

HTML_PATH = Path(__file__).parent / "index.html"


class FlightWatcherHandler(BaseHTTPRequestHandler):
    """HTTP request handler for local flight watcher dashboard and API."""

    watch_manager = WatchManager()

    def _send_json(self, data: Any, status: int = HTTPStatus.OK) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(payload)

    def _read_json_body(self) -> dict:
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length <= 0:
            return {}
        raw = self.rfile.read(content_length).decode("utf-8")
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        if path in ("/", "/index.html"):
            if not HTML_PATH.exists():
                self.send_error(HTTPStatus.NOT_FOUND, "Dashboard HTML not found")
                return

            with open(HTML_PATH, "rb") as f:
                content = f.read()

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        if path == "/api/watches":
            self.watch_manager.load()
            watches = [vars(w) for w in self.watch_manager.get_all()]
            for w in watches:
                w["mode"] = w["mode"].value if hasattr(w["mode"], "value") else str(w["mode"])
            self._send_json({"success": True, "watches": watches})
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Path not found")

    def do_POST(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        if path == "/api/watches":
            body = self._read_json_body()
            origin = body.get("origin", "").strip().upper()
            dest = body.get("destination", "").strip().upper()
            target_price = float(body.get("target_price", 0.0))
            mode_str = body.get("mode", "ARRIVAL_DEADLINE")
            trip_type = body.get("trip_type", "ROUND_TRIP").strip().upper()

            if not origin or not dest or target_price <= 0:
                self._send_json({"success": False, "error": "Origin, destination, and target price are required."}, 400)
                return

            try:
                mode = WatchMode(mode_str)
            except ValueError:
                mode = WatchMode.ARRIVAL_DEADLINE

            watch_id = body.get("id") or f"{origin.lower()}-{dest.lower()}-{str(abs(hash(origin+dest+mode_str)))[:5]}"

            new_watch = TargetedWatch(
                id=watch_id,
                origin=origin,
                destination=dest,
                destination_name=body.get("destination_name", ""),
                target_price=target_price,
                mode=mode,
                trip_type=trip_type,
                departure_date=body.get("departure_date") or None,
                return_date=body.get("return_date") or None,
                must_arrive_by=body.get("must_arrive_by") or None,
                window_start=body.get("window_start") or None,
                window_end=body.get("window_end") or None,
                min_nights=int(body.get("min_nights", 7)),
                max_nights=int(body.get("max_nights", 7)),
                cabin_class=body.get("cabin_class", "ECONOMY").upper(),
                max_stops=int(body.get("max_stops", 1)),
                notes=body.get("notes") or None,
            )

            auto_push = bool(body.get("auto_push", True))
            self.watch_manager.add(new_watch, auto_push=auto_push)
            self._send_json({"success": True, "watch": vars(new_watch)})
            return

        # Check endpoints like /api/watches/<id>/toggle, /api/watches/<id>/delete, /api/watches/<id>/check
        toggle_match = re.match(r"^/api/watches/([^/]+)/toggle$", path)
        if toggle_match:
            watch_id = toggle_match.group(1)
            new_state = self.watch_manager.toggle(watch_id, auto_push=True)
            if new_state is not None:
                self._send_json({"success": True, "active": new_state})
            else:
                self._send_json({"success": False, "error": "Watch not found"}, 404)
            return

        delete_match = re.match(r"^/api/watches/([^/]+)/delete$", path)
        if delete_match:
            watch_id = delete_match.group(1)
            if self.watch_manager.delete(watch_id, auto_push=True):
                self._send_json({"success": True, "deleted": watch_id})
            else:
                self._send_json({"success": False, "error": "Watch not found"}, 404)
            return

        check_match = re.match(r"^/api/watches/([^/]+)/check$", path)
        if check_match:
            watch_id = check_match.group(1)
            watch = next((w for w in self.watch_manager.get_all() if w.id == watch_id), None)
            if not watch:
                self._send_json({"success": False, "error": "Watch not found"}, 404)
                return

            scanner = TargetedTripScanner(watch_manager=self.watch_manager)
            deals = scanner.scan_watch(watch)
            self.watch_manager.save()
            deals_out = []
            for d in deals:
                deals_out.append({
                    "origin": d.origin,
                    "destination": d.destination,
                    "price": d.price,
                    "airline": d.airline,
                    "stops": d.stops,
                    "duration": d.formatted_duration,
                    "departure_date": d.departure_date,
                    "return_date": d.return_date,
                    "deep_link": d.deep_link,
                    "notes": d.notes,
                })
            self._send_json({
                "success": True,
                "watch_id": watch_id,
                "lowest_price": watch.lowest_price_found,
                "deals": deals_out,
            })
            return

        if path == "/api/sync":
            ok, msg = self.watch_manager.sync_git("chore(watches): sync from local dashboard")
            self._send_json({"success": ok, "message": msg})
            return

        if path == "/api/scan":
            scanner = TargetedTripScanner(watch_manager=self.watch_manager)
            deals = scanner.scan_all()
            self._send_json({
                "success": True,
                "count": len(deals),
                "deals": [
                    {
                        "origin": d.origin,
                        "destination": d.destination,
                        "price": d.price,
                        "airline": d.airline,
                        "departure_date": d.departure_date,
                        "return_date": d.return_date,
                        "deep_link": d.deep_link,
                    }
                    for d in deals
                ],
            })
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Path not found")

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress noisy access logging in terminal."""
        pass


def run_server(host: str = "0.0.0.0", port: int = 5000) -> None:
    """Run multithreaded HTTP server."""
    server_address = (host, port)
    httpd = ThreadingHTTPServer(server_address, FlightWatcherHandler)
    logger.info(f"Flight Watcher dashboard active at http://localhost:{port} (bound to {host}:{port})")
    httpd.serve_forever()
