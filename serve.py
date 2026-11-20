#!/usr/bin/env python3
"""Local flight watcher server, auto-startup installer, and background daemon."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Configure logging
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt="%H:%M:%S")
logger = logging.getLogger("serve")

PROJECT_DIR = Path(__file__).resolve().parent

# Windows Startup folder path when running inside WSL
WINDOWS_STARTUP_DIR = Path(
    "/mnt/c/Users/gabog/AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup"
)


def install_windows_startup() -> bool:
    """Install silent background auto-start VBS script in Windows Startup folder."""
    if not WINDOWS_STARTUP_DIR.exists():
        logger.error(f"Windows startup directory not found at: {WINDOWS_STARTUP_DIR}")
        print("[-] Could not locate Windows Startup folder. Ensure WSL has access to /mnt/c.")
        return False

    vbs_path = WINDOWS_STARTUP_DIR / "flights-watcher.vbs"
    # Command executes WSL headless in background (0 = hide window, False = don't block)
    vbs_content = (
        'Set WshShell = CreateObject("WScript.Shell")\n'
        f'WshShell.Run "wsl.exe -e python3 {PROJECT_DIR}/serve.py", 0, False\n'
    )

    try:
        with open(vbs_path, "w", encoding="utf-8") as f:
            f.write(vbs_content)

        print("[+] Success! Auto-start installed successfully.")
        print(f"    Startup script: {vbs_path}")
        print("    Every time your computer turns on, Flight Watcher will start automatically in the background.")
        print("    No terminal popup or manual launch needed.")
        return True
    except Exception as e:
        logger.error(f"Failed to write startup script: {e}")
        return False


def remove_windows_startup() -> bool:
    """Remove auto-start script from Windows Startup folder."""
    vbs_path = WINDOWS_STARTUP_DIR / "flights-watcher.vbs"
    if vbs_path.exists():
        try:
            vbs_path.unlink()
            print("[+] Removed auto-start script from Windows Startup folder.")
            return True
        except Exception as e:
            logger.error(f"Failed to remove startup script: {e}")
            return False
    else:
        print("[i] No startup script found in Windows Startup folder.")
        return True


def show_hosts_instructions() -> None:
    """Display instructions for aliasing localhost to http://flights.local:5000."""
    print("\n" + "=" * 65)
    print("🌐 HOW TO ACCESS VIA http://flights.local:5000")
    print("=" * 65)
    print("To open http://flights.local:5000 instead of remembering localhost:")
    print("1. In Windows, open Notepad as Administrator.")
    print(r"2. Open file: C:\Windows\System32\drivers\etc\hosts")
    print("3. Add this line at the bottom:")
    print("   127.0.0.1    flights.local")
    print("4. Save the file. You can now visit: http://flights.local:5000")
    print("   (Or simply bookmark: http://localhost:5000)")
    print("=" * 65 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Local Flight Watcher Server & Daemon")
    parser.add_argument("--port", type=int, default=5000, help="Port to bind (default: 5000)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host interface (default: 0.0.0.0)")
    parser.add_argument(
        "--install-startup",
        action="store_true",
        help="Install auto-start script to Windows Startup folder so it boots with computer",
    )
    parser.add_argument(
        "--remove-startup",
        action="store_true",
        help="Remove auto-start script from Windows Startup folder",
    )
    parser.add_argument(
        "--setup-alias",
        action="store_true",
        help="Show instructions for mapping flights.local to 127.0.0.1",
    )
    parser.add_argument(
        "--no-telegram",
        action="store_true",
        help="Disable interactive Telegram bot polling",
    )

    args = parser.parse_args()

    if args.install_startup:
        install_windows_startup()
        show_hosts_instructions()
        return

    if args.remove_startup:
        remove_windows_startup()
        return

    if args.setup_alias:
        show_hosts_instructions()
        return

    # Start Telegram Interactive Bot in background
    if not args.no_telegram:
        try:
            from src.telegram_bot import TelegramWatchBot
            bot = TelegramWatchBot()
            bot.start_in_background()
        except Exception as e:
            logger.warning(f"Could not initialize Telegram bot listener: {e}")

    # Start Web Dashboard
    from src.web.server import run_server

    print("=" * 65)
    print(f"🚀 Flight Watcher Dashboard running at http://localhost:{args.port}")
    print(f"   Network accessible at: http://127.0.0.1:{args.port} or http://flights.local:{args.port}")
    print(f"   Telegram bot listener: {'Disabled' if args.no_telegram else 'Active'}")
    print("   Press Ctrl+C to stop.")
    print("=" * 65)

    try:
        run_server(host=args.host, port=args.port)
    except KeyboardInterrupt:
        print("\n[+] Flight Watcher stopped cleanly.")


if __name__ == "__main__":
    main()
