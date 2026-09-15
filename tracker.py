#!/usr/bin/env python3
"""
Signal — macOS Activity Tracker

Polls the active app, window title, and (for browsers) the active tab URL
every N seconds via AppleScript. Logs to SQLite at ~/.signal/activity.db.

Usage:
    python tracker.py
    python tracker.py --interval 10 --db /tmp/signal.db
"""

import argparse
import signal as signal_module
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path.home() / ".signal" / "activity.db"
DEFAULT_INTERVAL = 600

_running = True

# ---------------------------------------------------------------------------
# AppleScript templates
# ---------------------------------------------------------------------------

# Generic: get frontmost app name + its front window title
_SCRIPT_GENERIC = """\
tell application "System Events"
    set frontProc to first application process whose frontmost is true
    set appName to name of frontProc
end tell
set winTitle to ""
try
    tell application appName
        set winTitle to name of front window
    end tell
end try
return appName & "|||" & winTitle & "|||"
"""

# Browser-specific scripts return: appName ||| tab title ||| tab URL
_BROWSER_SCRIPTS: dict[str, str] = {
    "Google Chrome": """\
tell application "Google Chrome"
    set t to title of active tab of front window
    set u to URL of active tab of front window
    return "Google Chrome|||" & t & "|||" & u
end tell
""",
    "Google Chrome Beta": """\
tell application "Google Chrome Beta"
    set t to title of active tab of front window
    set u to URL of active tab of front window
    return "Google Chrome Beta|||" & t & "|||" & u
end tell
""",
    "Chromium": """\
tell application "Chromium"
    set t to title of active tab of front window
    set u to URL of active tab of front window
    return "Chromium|||" & t & "|||" & u
end tell
""",
    "Safari": """\
tell application "Safari"
    set t to name of current tab of front window
    set u to URL of current tab of front window
    return "Safari|||" & t & "|||" & u
end tell
""",
    "Firefox": """\
tell application "Firefox"
    set winTitle to ""
    try
        set winTitle to name of front window
    end try
    return "Firefox|||" & winTitle & "|||"
end tell
""",
    "Arc": """\
tell application "Arc"
    set t to title of active tab of front window
    set u to URL of active tab of front window
    return "Arc|||" & t & "|||" & u
end tell
""",
    "Brave Browser": """\
tell application "Brave Browser"
    set t to title of active tab of front window
    set u to URL of active tab of front window
    return "Brave Browser|||" & t & "|||" & u
end tell
""",
    "Microsoft Edge": """\
tell application "Microsoft Edge"
    set t to title of active tab of front window
    set u to URL of active tab of front window
    return "Microsoft Edge|||" & t & "|||" & u
end tell
""",
}


def _run_applescript(script: str) -> str | None:
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=4,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except subprocess.TimeoutExpired:
        pass
    except Exception:
        pass
    return None


def get_active_window() -> tuple[str, str, str]:
    """
    Return (app_name, window_title, url).
    For browsers: url is the active tab's URL.
    For everything else: url is empty string.
    """
    # First find the frontmost app name
    raw = _run_applescript(_SCRIPT_GENERIC)
    if not raw:
        return "", "", ""

    parts = raw.split("|||", 2)
    app_name = parts[0].strip() if parts else ""
    generic_title = parts[1].strip() if len(parts) > 1 else ""

    if not app_name:
        return "", "", ""

    # If it's a known browser, use the browser-specific script for tab info
    if app_name in _BROWSER_SCRIPTS:
        browser_raw = _run_applescript(_BROWSER_SCRIPTS[app_name])
        if browser_raw:
            bparts = browser_raw.split("|||", 2)
            tab_title = bparts[1].strip() if len(bparts) > 1 else generic_title
            tab_url   = bparts[2].strip() if len(bparts) > 2 else ""
            return app_name, tab_title, tab_url

    return app_name, generic_title, ""


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def open_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS activity (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp        TEXT    NOT NULL,
            app_name         TEXT    NOT NULL,
            window_title     TEXT    NOT NULL DEFAULT '',
            url              TEXT    NOT NULL DEFAULT '',
            interval_seconds INTEGER NOT NULL
        )
        """
    )
    # Migrate existing DBs that don't have the url column yet
    cols = {row[1] for row in conn.execute("PRAGMA table_info(activity)")}
    if "url" not in cols:
        conn.execute("ALTER TABLE activity ADD COLUMN url TEXT NOT NULL DEFAULT ''")

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_activity_timestamp ON activity(timestamp)"
    )
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Signal handling
# ---------------------------------------------------------------------------

def _handle_signal(signum, frame):
    global _running
    print(f"\n[Signal] Received signal {signum} — shutting down gracefully…", flush=True)
    _running = False


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Signal Activity Tracker — polls active macOS app every N seconds"
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL,
        metavar="SECONDS",
        help=f"Polling interval in seconds (default: {DEFAULT_INTERVAL})",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        metavar="PATH",
        help=f"SQLite database path (default: {DEFAULT_DB})",
    )
    args = parser.parse_args()

    signal_module.signal(signal_module.SIGINT, _handle_signal)
    signal_module.signal(signal_module.SIGTERM, _handle_signal)

    conn = open_db(args.db)
    print(f"[Signal] Tracker started  |  DB: {args.db}  |  Interval: {args.interval}s", flush=True)
    print("[Signal] Press Ctrl+C to stop.\n", flush=True)

    last_app = ""
    last_title = ""
    last_url = ""

    try:
        while _running:
            app_name, window_title, url = get_active_window()

            if app_name:
                ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
                conn.execute(
                    "INSERT INTO activity (timestamp, app_name, window_title, url, interval_seconds)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (ts, app_name, window_title, url, args.interval),
                )
                conn.commit()

                if app_name != last_app or window_title != last_title or url != last_url:
                    display = f"{app_name!r}"
                    if window_title:
                        display += f"  —  {window_title!r}"
                    if url:
                        display += f"\n          {url}"
                    print(f"[Signal] {ts}  {display}", flush=True)
                    last_app, last_title, last_url = app_name, window_title, url

            # Sleep in small chunks so SIGTERM is handled promptly
            for _ in range(args.interval * 10):
                if not _running:
                    break
                time.sleep(0.1)

    finally:
        conn.close()
        print("[Signal] Tracker stopped.", flush=True)


if __name__ == "__main__":
    main()
