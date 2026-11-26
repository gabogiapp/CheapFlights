#!/usr/bin/env python3
"""Local flight watcher server, auto-startup installer, and background daemon."""

from __future__ import annotations

import argparse
import logging
import os
import socket
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
# Always run from project root so relative paths (data/watches.json, .env, git) work from any caller
os.chdir(PROJECT_DIR)
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

LOG_FILE = Path("/tmp/flights-watcher.log")

# Configure logging to both console and /tmp/flights-watcher.log for headless diagnostics
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
handlers: list[logging.Handler] = [logging.StreamHandler()]
try:
    handlers.append(logging.FileHandler(LOG_FILE, encoding="utf-8"))
except Exception:
    pass

logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt="%H:%M:%S", handlers=handlers)
logger = logging.getLogger("serve")

# Windows paths when running inside WSL
WINDOWS_STARTUP_DIR = Path(
    "/mnt/c/Users/gabog/AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup"
)
POWERSHELL_EXE = Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
TASK_NAME = "FlightsWatcherAutoStart"


def is_port_listening(port: int, host: str = "127.0.0.1") -> bool:
    """Return True if a local server is already listening on the given port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def install_windows_startup() -> bool:
    """Install silent background auto-start VBS script and Windows Scheduled Task."""
    if not WINDOWS_STARTUP_DIR.exists():
        logger.error(f"Windows startup directory not found at: {WINDOWS_STARTUP_DIR}")
        print("[-] Could not locate Windows Startup folder. Ensure WSL has access to /mnt/c.")
        return False

    distro = os.environ.get("WSL_DISTRO_NAME", "Ubuntu")
    vbs_path = WINDOWS_STARTUP_DIR / "flights-watcher.vbs"
    # Explicitly specify distro, working directory (--cd), and full python3 + script paths
    wsl_cmd = f"wsl.exe -d {distro} --cd {PROJECT_DIR} -e /usr/bin/python3 {PROJECT_DIR}/serve.py"
    vbs_content = (
        'Set WshShell = CreateObject("WScript.Shell")\n'
        f'WshShell.Run "{wsl_cmd}", 0, False\n'
    )

    try:
        with open(vbs_path, "w", encoding="utf-8") as f:
            f.write(vbs_content)
        print(f"[+] Updated Windows Startup script: {vbs_path}")
    except Exception as e:
        logger.error(f"Failed to write startup script: {e}")
        return False

    # Also register a Windows Scheduled Task (runs at Log On with 15s delay + periodic watchdog every 10 min)
    # This handles Windows Fast Startup, sleep/wake cycles, and WSL/Docker restarts reliably.
    if POWERSHELL_EXE.exists():
        win_vbs = r"C:\Users\gabog\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\flights-watcher.vbs"
        ps_script = f"""
$action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument '"{win_vbs}"'
$tLogon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$tLogon.Delay = 'PT15S'
$tRepeat = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 10)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -StartWhenAvailable
Register-ScheduledTask -TaskName '{TASK_NAME}' -Action $action -Trigger @($tLogon, $tRepeat) -Settings $settings -Force | Out-Null
Write-Output 'TASK_OK'
"""
        try:
            res = subprocess.run(
                [str(POWERSHELL_EXE), "-NoProfile", "-NonInteractive", "-Command", ps_script],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=15,
            )
            if res.returncode == 0 and "TASK_OK" in res.stdout:
                print(f"[+] Registered Windows Scheduled Task '{TASK_NAME}' (at logon + watchdog).")
            else:
                logger.warning(f"Scheduled task registration note: {res.stderr.strip() or res.stdout.strip()}")
                print(f"[!] Note: Scheduled task registration returned: {res.stderr.strip() or res.stdout.strip()}")
        except Exception as e:
            logger.warning(f"Could not register scheduled task: {e}")

    print("    Flight Watcher will start silently on login and stay online automatically.")
    return True


def remove_windows_startup() -> bool:
    """Remove auto-start script from Windows Startup folder and Task Scheduler."""
    vbs_path = WINDOWS_STARTUP_DIR / "flights-watcher.vbs"
    if vbs_path.exists():
        try:
            vbs_path.unlink()
            print("[+] Removed auto-start script from Windows Startup folder.")
        except Exception as e:
            logger.error(f"Failed to remove startup script: {e}")
            return False
    else:
        print("[i] No startup script found in Windows Startup folder.")

    if POWERSHELL_EXE.exists():
        try:
            subprocess.run(
                [
                    str(POWERSHELL_EXE),
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    f"Unregister-ScheduledTask -TaskName '{TASK_NAME}' -Confirm:$false -ErrorAction SilentlyContinue",
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=10,
            )
            print(f"[+] Removed Windows Scheduled Task '{TASK_NAME}' (if present).")
        except Exception:
            pass
    return True


def show_hosts_instructions() -> None:
    """Display instructions for aliasing localhost to http://flights.local:5000."""
    print("\n" + "=" * 65)
    print("HOW TO ACCESS VIA http://flights.local:5000")
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
        help="Install auto-start script and scheduled task so it boots with computer",
    )
    parser.add_argument(
        "--remove-startup",
        action="store_true",
        help="Remove auto-start script and scheduled task",
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

    # Load .env explicitly from project root before importing/starting components
    try:
        from dotenv import load_dotenv
        load_dotenv(PROJECT_DIR / ".env")
    except Exception:
        pass

    # Avoid duplicate instance / port-in-use error when watchdog/startup triggers while already running
    if is_port_listening(args.port):
        logger.info(f"Flight Watcher is already running on port {args.port}; exiting duplicate launch.")
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
    print(f"Flight Watcher Dashboard running at http://localhost:{args.port}")
    print(f"   Working directory: {os.getcwd()}")
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
