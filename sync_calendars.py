#!/usr/bin/env python3
"""
Signal — Calendar Sync

Pulls events from Google Calendar and/or Notion into the Signal SQLite DB.

Usage:
    python sync_calendars.py                     # sync both
    python sync_calendars.py --source google
    python sync_calendars.py --source notion
    python sync_calendars.py --db /custom/path.db
"""

import argparse
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

CONFIG_DIR = Path.home() / ".signal"
DEFAULT_DB = CONFIG_DIR / "activity.db"
GOOGLE_CREDS_FILE = CONFIG_DIR / "google_creds.json"  # OAuth2 client secrets
GOOGLE_TOKEN_FILE = CONFIG_DIR / "google_token.json"  # Saved token (auto-created)
CONFIG_FILE = CONFIG_DIR / "config.json"              # { notion_token, notion_calendar_db_id }


# ---------------------------------------------------------------------------
# DB setup
# ---------------------------------------------------------------------------

def ensure_calendar_table(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS calendar_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT    NOT NULL DEFAULT '',
            start_time  TEXT,
            end_time    TEXT,
            source      TEXT    NOT NULL,
            description TEXT    DEFAULT '',
            external_id TEXT,
            UNIQUE(external_id, source) ON CONFLICT REPLACE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_cal_start ON calendar_events(start_time)"
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Google Calendar
# ---------------------------------------------------------------------------

def sync_google(conn: sqlite3.Connection, days_back: int = 30, days_ahead: int = 30):
    try:
        import google.auth.transport.requests
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        print(
            "[Signal] Google libraries missing. Install with:\n"
            "  pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib"
        )
        return

    SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
    creds = None

    if GOOGLE_TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(GOOGLE_TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("[Signal] Refreshing Google token…")
            creds.refresh(google.auth.transport.requests.Request())
        else:
            if not GOOGLE_CREDS_FILE.exists():
                print(
                    f"[Signal] Google OAuth2 credentials not found at {GOOGLE_CREDS_FILE}\n"
                    "  Download client_secret_*.json from Google Cloud Console,\n"
                    f"  rename it to google_creds.json, and place it at {GOOGLE_CREDS_FILE}"
                )
                return
            flow = InstalledAppFlow.from_client_secrets_file(str(GOOGLE_CREDS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)

        GOOGLE_TOKEN_FILE.write_text(creds.to_json())
        print(f"[Signal] Google token saved to {GOOGLE_TOKEN_FILE}")

    service = build("calendar", "v3", credentials=creds)

    now = datetime.now(timezone.utc)
    time_min = (now - timedelta(days=days_back)).isoformat()
    time_max = (now + timedelta(days=days_ahead)).isoformat()

    events_result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            maxResults=1000,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    events = events_result.get("items", [])
    count = 0
    for event in events:
        title = event.get("summary", "(No Title)")
        start_raw = event.get("start", {})
        end_raw = event.get("end", {})
        start = start_raw.get("dateTime") or start_raw.get("date", "")
        end = end_raw.get("dateTime") or end_raw.get("date", "")
        description = event.get("description", "") or ""
        external_id = event.get("id", "")
        conn.execute(
            """
            INSERT OR REPLACE INTO calendar_events
                (title, start_time, end_time, source, description, external_id)
            VALUES (?, ?, ?, 'google', ?, ?)
            """,
            (title, start, end, description[:2000], external_id),
        )
        count += 1

    conn.commit()
    print(f"[Signal] Google Calendar  →  {count} events synced.")


# ---------------------------------------------------------------------------
# Notion
# ---------------------------------------------------------------------------

def _text_from_rich(rich_text_array: list) -> str:
    return "".join(block.get("plain_text", "") for block in rich_text_array)


def sync_notion(conn: sqlite3.Connection):
    try:
        from notion_client import Client
    except ImportError:
        print("[Signal] Notion library missing. Install with:  pip install notion-client")
        return

    if not CONFIG_FILE.exists():
        print(
            f"[Signal] Config file not found at {CONFIG_FILE}\n"
            "  Create it with at minimum: { \"notion_token\": \"secret_…\" }"
        )
        return

    config = json.loads(CONFIG_FILE.read_text())
    notion_token = config.get("notion_token", "").strip()
    db_id = config.get("notion_calendar_db_id", "").strip()

    if not notion_token:
        print("[Signal] notion_token missing in config.json — skipping Notion sync.")
        return
    if not db_id:
        print(
            "[Signal] notion_calendar_db_id missing in config.json.\n"
            "  Add the Notion database ID that holds your calendar/tasks.\n"
            "  (Found in the DB page URL: notion.so/<workspace>/<DATABASE_ID>?v=…)"
        )
        return

    client = Client(auth=notion_token)
    try:
        pages = []
        cursor = None
        while True:
            kwargs = {"database_id": db_id, "page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = client.databases.query(**kwargs)
            pages.extend(resp.get("results", []))
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
    except Exception as exc:
        print(f"[Signal] Notion query failed: {exc}")
        return

    # Property-name guessing for common Notion calendar schemas
    TITLE_KEYS = ["Name", "Title", "Task", "Event", "Summary"]
    DATE_KEYS = ["Date", "Due", "When", "Start", "Deadline", "Schedule"]

    count = 0
    for page in pages:
        props = page.get("properties", {})

        title = ""
        for key in TITLE_KEYS:
            if key in props and props[key].get("type") == "title":
                title = _text_from_rich(props[key].get("title", []))
                break
        # Fallback: find any title-type property
        if not title:
            for val in props.values():
                if val.get("type") == "title":
                    title = _text_from_rich(val.get("title", []))
                    break

        start_time = ""
        end_time = ""
        for key in DATE_KEYS:
            if key in props and props[key].get("type") == "date":
                date_obj = props[key].get("date") or {}
                start_time = date_obj.get("start", "")
                end_time = date_obj.get("end", "") or start_time
                break

        external_id = page.get("id", "")
        description = page.get("url", "")

        conn.execute(
            """
            INSERT OR REPLACE INTO calendar_events
                (title, start_time, end_time, source, description, external_id)
            VALUES (?, ?, ?, 'notion', ?, ?)
            """,
            (title or "(Untitled)", start_time, end_time, description, external_id),
        )
        count += 1

    conn.commit()
    print(f"[Signal] Notion           →  {count} pages synced.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Signal Calendar Sync")
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        metavar="PATH",
        help=f"SQLite DB path (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--source",
        choices=["google", "notion", "all"],
        default="all",
        help="Which source to sync (default: all)",
    )
    parser.add_argument(
        "--days-back",
        type=int,
        default=30,
        help="How many days back to pull events (default: 30)",
    )
    parser.add_argument(
        "--days-ahead",
        type=int,
        default=30,
        help="How many days ahead to pull events (default: 30)",
    )
    args = parser.parse_args()

    args.db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(args.db))
    conn.execute("PRAGMA journal_mode=WAL")
    ensure_calendar_table(conn)

    if args.source in ("google", "all"):
        sync_google(conn, days_back=args.days_back, days_ahead=args.days_ahead)
    if args.source in ("notion", "all"):
        sync_notion(conn)

    conn.close()
    print("[Signal] Sync complete.")


if __name__ == "__main__":
    main()
