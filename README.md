# Signal — macOS Productivity Auditor

A fully local productivity tracker that watches what you're working on, syncs calendar data, and uses Claude AI to identify your #1 productivity constraint using the Theory of Constraints framework.

All data stays on your machine at `~/.signal/`.

---

## Components

| File | Role |
|------|------|
| `tracker.py` | Background daemon — polls active app every 5 s via AppleScript |
| `sync_calendars.py` | Syncs Google Calendar & Notion events into the DB |
| `dashboard/app.py` | Flask web server serving the dashboard on port 5000 |
| `setup.sh` | One-shot setup: deps, credentials, launchd autostart |

---

## Requirements

- macOS (Monterey 12+ recommended)
- Python 3.10+
- An [Anthropic API key](https://console.anthropic.com/settings/keys) for AI analysis

---

## Quick Start

```bash
git clone <this-repo> ~/Projects/Signal_mk2
cd ~/Projects/Signal_mk2
chmod +x setup.sh
./setup.sh
```

The setup script will:
1. Install Python dependencies
2. Walk through Google OAuth2 setup (browser opens)
3. Ask for your Notion integration token
4. Register `tracker.py` as a launchd service that starts on login

Then open the dashboard:

```bash
cd dashboard
python app.py
# → http://127.0.0.1:5000
```

---

## Manual Setup (step by step)

### 1. Create the data directory

```bash
mkdir -p ~/.signal
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Set your Anthropic API key

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
# Add this to ~/.zshrc so it persists
```

### 4. Google Calendar OAuth2

1. Go to [Google Cloud Console](https://console.cloud.google.com/) → Create a project
2. Enable the **Google Calendar API**
3. Go to **Credentials** → **Create Credentials** → **OAuth 2.0 Client IDs** → **Desktop App**
4. Download the JSON file and save it as `~/.signal/google_creds.json`
5. Run the first-time auth flow:
   ```bash
   python sync_calendars.py --source google
   ```
   A browser window opens — sign in and grant read access. The token is saved to `~/.signal/google_token.json` and auto-refreshed on future runs.

### 5. Notion Setup

1. Go to [notion.so/my-integrations](https://www.notion.so/my-integrations) → Create integration → Copy **Internal Integration Token**
2. Open your Notion calendar/tasks database → **Share** → add your integration
3. Copy the database ID from the URL:
   ```
   https://www.notion.so/myworkspace/<DATABASE_ID>?v=...
   ```
4. Create `~/.signal/config.json`:
   ```json
   {
     "notion_token": "secret_...",
     "notion_calendar_db_id": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
   }
   ```
5. Sync:
   ```bash
   python sync_calendars.py --source notion
   ```

### 6. Run the tracker manually

```bash
python tracker.py
python tracker.py --interval 10          # poll every 10 s
python tracker.py --db /tmp/test.db      # custom DB path
```

### 7. Register as a launchd service (auto-start on login)

The plist is generated and installed by `setup.sh`. To do it manually:

```bash
# Edit com.signal.tracker.plist with correct Python and script paths, then:
cp com.signal.tracker.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/com.signal.tracker.plist
```

To stop and unregister:

```bash
launchctl unload ~/Library/LaunchAgents/com.signal.tracker.plist
```

---

## Database Schema

**`~/.signal/activity.db`**

```sql
-- Raw activity log
CREATE TABLE activity (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp        TEXT    NOT NULL,  -- ISO-8601 UTC
    app_name         TEXT    NOT NULL,
    window_title     TEXT    NOT NULL DEFAULT '',
    interval_seconds INTEGER NOT NULL
);

-- Calendar events from Google / Notion
CREATE TABLE calendar_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL DEFAULT '',
    start_time  TEXT,
    end_time    TEXT,
    source      TEXT    NOT NULL,  -- 'google' | 'notion'
    description TEXT    DEFAULT '',
    external_id TEXT,
    UNIQUE(external_id, source)
);
```

---

## Dashboard Features

| Feature | Details |
|---------|---------|
| Top Apps | Bar chart of apps by total minutes in selected date range |
| Hourly Heatmap | 24-hour activity breakdown for the selected start date |
| 7-Day Trend | Hours tracked per day for the last week |
| Date Range Picker | Filter all charts to any date range |
| Analyze Bottlenecks | Sends aggregated data to Claude → streams Theory of Constraints analysis |

---

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `ANTHROPIC_API_KEY` | *(required for AI)* | Your Anthropic API key |
| `SIGNAL_DB` | `~/.signal/activity.db` | Override DB path for dashboard |
| `SIGNAL_PORT` | `5000` | Dashboard port |

---

## Privacy

- No screenshots. No screen recording. Window titles are captured via AppleScript only.
- Everything is stored locally at `~/.signal/`. Nothing is sent anywhere except:
  - Google Calendar API (OAuth2, read-only)
  - Notion API (read-only)
  - Anthropic API (aggregated app-usage + calendar data for AI analysis, only when you click "Analyze Bottlenecks")

---

## Troubleshooting

**Tracker logs:**
```bash
tail -f ~/.signal/tracker.log
tail -f ~/.signal/tracker.error.log
```

**Check if tracker is running:**
```bash
launchctl list | grep signal
```

**Re-sync calendars:**
```bash
python sync_calendars.py
```

**Reset Google auth (if token expired/revoked):**
```bash
rm ~/.signal/google_token.json
python sync_calendars.py --source google
```
