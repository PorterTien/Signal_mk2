#!/usr/bin/env python3
"""
Signal — Web Dashboard (Flask)

Serves a local dark-themed productivity dashboard on http://127.0.0.1:5000
Reads from ~/.signal/activity.db and streams AI analysis via Anthropic API.

Usage:
    cd dashboard && python app.py
    SIGNAL_PORT=8080 python app.py
    SIGNAL_DB=/custom/path.db python app.py
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anthropic
from flask import Flask, Response, jsonify, render_template, request, stream_with_context

app = Flask(__name__)

DEFAULT_DB = Path.home() / ".signal" / "activity.db"


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_db() -> sqlite3.Connection:
    db_path = os.environ.get("SIGNAL_DB", str(DEFAULT_DB))
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
    )


# ---------------------------------------------------------------------------
# Routes — static
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# Routes — data API
# ---------------------------------------------------------------------------

@app.route("/api/top-apps")
def top_apps():
    """Top apps by total tracked minutes within an optional date range."""
    start = request.args.get("start")
    end = request.args.get("end")
    limit = min(int(request.args.get("limit", 20)), 50)

    conn = get_db()
    try:
        if not table_exists(conn, "activity"):
            return jsonify([])

        where, params = _date_where("timestamp", start, end)
        rows = conn.execute(
            f"""
            SELECT app_name,
                   ROUND(SUM(interval_seconds) / 60.0, 1) AS minutes,
                   COUNT(*)                                AS samples
            FROM activity
            {where}
            GROUP BY app_name
            ORDER BY minutes DESC
            LIMIT {limit}
            """,
            params,
        ).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


@app.route("/api/hourly-heatmap")
def hourly_heatmap():
    """Minutes of tracked activity per hour for a given date (defaults today)."""
    date_str = request.args.get("date", datetime.now().strftime("%Y-%m-%d"))

    conn = get_db()
    try:
        if not table_exists(conn, "activity"):
            return jsonify({str(h).zfill(2): 0 for h in range(24)})

        rows = conn.execute(
            """
            SELECT strftime('%H', timestamp) AS hour,
                   ROUND(SUM(interval_seconds) / 60.0, 1) AS minutes
            FROM activity
            WHERE date(timestamp) = ?
            GROUP BY hour
            """,
            (date_str,),
        ).fetchall()

        heatmap = {str(h).zfill(2): 0.0 for h in range(24)}
        for row in rows:
            heatmap[row["hour"]] = row["minutes"]
        return jsonify(heatmap)
    finally:
        conn.close()


@app.route("/api/seven-day-trend")
def seven_day_trend():
    """Total tracked hours per day for the last 7 days."""
    conn = get_db()
    try:
        if not table_exists(conn, "activity"):
            return jsonify([])

        rows = conn.execute(
            """
            SELECT date(timestamp)                         AS day,
                   ROUND(SUM(interval_seconds) / 3600.0, 2) AS hours
            FROM activity
            WHERE timestamp >= date('now', '-6 days')
            GROUP BY day
            ORDER BY day
            """
        ).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


@app.route("/api/window-titles")
def window_titles():
    """Top window titles for a given app within an optional date range."""
    app_name = request.args.get("app", "")
    start = request.args.get("start")
    end = request.args.get("end")
    limit = min(int(request.args.get("limit", 30)), 100)

    conn = get_db()
    try:
        if not table_exists(conn, "activity"):
            return jsonify([])

        clauses = ["app_name = ?", "window_title != ''"]
        params = [app_name]
        if start:
            clauses.append("timestamp >= ?")
            params.append(start)
        if end:
            clauses.append("timestamp <= ?")
            params.append(end)
        where = "WHERE " + " AND ".join(clauses)
        rows = conn.execute(
            f"""
            SELECT window_title,
                   MAX(url)                                AS url,
                   ROUND(SUM(interval_seconds) / 60.0, 1) AS minutes,
                   COUNT(*)                               AS samples
            FROM activity
            {where}
            GROUP BY window_title
            ORDER BY minutes DESC
            LIMIT {limit}
            """,
            params,
        ).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


@app.route("/api/calendar-events")
def calendar_events():
    """Calendar events within an optional date range."""
    start = request.args.get("start")
    end = request.args.get("end")

    conn = get_db()
    try:
        if not table_exists(conn, "calendar_events"):
            return jsonify([])

        where, params = _date_where("start_time", start, end)
        rows = conn.execute(
            f"SELECT * FROM calendar_events {where} ORDER BY start_time LIMIT 200",
            params,
        ).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


@app.route("/api/stats-summary")
def stats_summary():
    """Quick stats card: total hours today, total events today."""
    conn = get_db()
    today = datetime.now().strftime("%Y-%m-%d")
    result = {"hours_today": 0.0, "events_today": 0, "db_days": 0}
    try:
        if table_exists(conn, "activity"):
            row = conn.execute(
                "SELECT ROUND(SUM(interval_seconds)/3600.0, 1) AS h FROM activity WHERE date(timestamp)=?",
                (today,),
            ).fetchone()
            result["hours_today"] = (row["h"] or 0.0) if row else 0.0

            row2 = conn.execute(
                "SELECT COUNT(DISTINCT date(timestamp)) AS d FROM activity"
            ).fetchone()
            result["db_days"] = (row2["d"] or 0) if row2 else 0

        if table_exists(conn, "calendar_events"):
            row3 = conn.execute(
                "SELECT COUNT(*) AS c FROM calendar_events WHERE date(start_time)=?",
                (today,),
            ).fetchone()
            result["events_today"] = (row3["c"] or 0) if row3 else 0
    finally:
        conn.close()
    return jsonify(result)


# ---------------------------------------------------------------------------
# AI analysis — SSE stream
# ---------------------------------------------------------------------------

@app.route("/api/analyze", methods=["POST"])
def analyze():
    """
    Accepts aggregated data, calls Claude with Theory of Constraints framing,
    and streams the response as Server-Sent Events.
    """
    payload = request.json or {}
    top_apps_data = payload.get("top_apps", [])
    events_data = payload.get("calendar_events", [])
    date_range = payload.get("date_range", "the selected period")

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return jsonify({"error": "ANTHROPIC_API_KEY environment variable is not set."}), 500

    apps_lines = "\n".join(
        f"  • {a['app_name']}: {a['minutes']} min  ({a['samples']} samples)"
        for a in top_apps_data[:15]
    ) or "  (no activity data)"

    events_lines = "\n".join(
        f"  • [{e.get('source','?')}] {e.get('title','?')} — "
        f"{e.get('start_time','')[:16]} → {e.get('end_time','')[:16]}"
        for e in events_data[:40]
    ) or "  (no calendar events)"

    prompt = f"""You are a productivity consultant who applies the Theory of Constraints (ToC) to knowledge-worker workflows.

Below is macOS activity-tracker data and calendar events for {date_range}.

## App Usage (by tracked minutes)
{apps_lines}

## Calendar Events
{events_lines}

---

Using the Theory of Constraints framework (Identify → Exploit → Subordinate → Elevate → Repeat), do the following:

1. **Identify the #1 constraint**: Name the single limiting factor that most restricts the user's effective output. Use the actual data — cite specific app names, time figures, and scheduling patterns.

2. **Explain why it's the constraint**: Show the reasoning. What does the usage pattern reveal about fragmented focus, context switching, or misaligned calendar commitments?

3. **Exploit the constraint (quick wins)**: 2–3 specific, immediately actionable steps to get more out of the existing constraint without large changes.

4. **Elevate the constraint (structural fix)**: One deeper change — workflow, tooling, or scheduling — that would permanently remove or raise the constraint.

5. **Secondary bottlenecks**: Briefly flag 1–2 other patterns worth watching.

Be direct and data-driven. No generic platitudes. Every recommendation must be traceable back to the data above."""

    def generate():
        client = anthropic.Anthropic(api_key=api_key)
        try:
            with client.messages.stream(
                model="claude-opus-4-6",
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                for text in stream.text_stream:
                    yield f"data: {json.dumps({'text': text})}\n\n"
        except anthropic.APIError as exc:
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _date_where(col: str, start: str | None, end: str | None) -> tuple[str, list]:
    clauses, params = [], []
    if start:
        clauses.append(f"{col} >= ?")
        params.append(start)
    if end:
        clauses.append(f"{col} <= ?")
        params.append(end)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("SIGNAL_PORT", 5000))
    print(f"[Signal] Dashboard running at http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=False)
