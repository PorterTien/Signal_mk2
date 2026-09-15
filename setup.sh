#!/usr/bin/env bash
# Signal — Setup Script
# Installs dependencies, configures credentials, and registers launchd autostart.
set -euo pipefail

# ── Colours ────────────────────────────────────────────────────────────────
BOLD=$(tput bold 2>/dev/null || true)
RESET=$(tput sgr0 2>/dev/null || true)
GREEN=$(tput setaf 2 2>/dev/null || true)
YELLOW=$(tput setaf 3 2>/dev/null || true)
CYAN=$(tput setaf 6 2>/dev/null || true)
RED=$(tput setaf 1 2>/dev/null || true)

info()    { echo "${CYAN}${BOLD}[Signal]${RESET} $*"; }
success() { echo "${GREEN}${BOLD}[Signal]${RESET} $*"; }
warn()    { echo "${YELLOW}${BOLD}[Signal]${RESET} $*"; }
error()   { echo "${RED}${BOLD}[Signal]${RESET} $*" >&2; }

# ── Paths ──────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SIGNAL_DIR="$HOME/.signal"
TRACKER_PATH="$SCRIPT_DIR/tracker.py"
PLIST_SRC="$SCRIPT_DIR/com.signal.tracker.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/com.signal.tracker.plist"
CONFIG_FILE="$SIGNAL_DIR/config.json"
GOOGLE_CREDS="$SIGNAL_DIR/google_creds.json"

echo ""
echo "${BOLD}╔══════════════════════════════════════════╗${RESET}"
echo "${BOLD}║          Signal Setup Wizard             ║${RESET}"
echo "${BOLD}╚══════════════════════════════════════════╝${RESET}"
echo ""

# ── 1. macOS check ─────────────────────────────────────────────────────────
if [[ "$(uname)" != "Darwin" ]]; then
  error "Signal requires macOS. Exiting."
  exit 1
fi
success "macOS detected."

# ── 2. Python 3.10+ check ──────────────────────────────────────────────────
PYTHON=$(command -v python3 || true)
if [[ -z "$PYTHON" ]]; then
  error "python3 not found. Install it from https://python.org or via Homebrew: brew install python"
  exit 1
fi

PYVER=$("$PYTHON" -c "import sys; print(sys.version_info[:2])")
PYMAJOR=$("$PYTHON" -c "import sys; print(sys.version_info[0])")
PYMINOR=$("$PYTHON" -c "import sys; print(sys.version_info[1])")
if [[ $PYMAJOR -lt 3 || ( $PYMAJOR -eq 3 && $PYMINOR -lt 10 ) ]]; then
  error "Python 3.10+ required. Found: $("$PYTHON" --version)"
  exit 1
fi
success "Python $("$PYTHON" --version) OK."

# ── 3. Create ~/.signal ─────────────────────────────────────────────────────
mkdir -p "$SIGNAL_DIR"
success "Created data directory: $SIGNAL_DIR"

# ── 4. Install Python deps ──────────────────────────────────────────────────
info "Installing Python dependencies…"
"$PYTHON" -m pip install --quiet --upgrade pip
"$PYTHON" -m pip install --quiet -r "$SCRIPT_DIR/requirements.txt"
success "Python dependencies installed."

# ── 5. ANTHROPIC_API_KEY ───────────────────────────────────────────────────
echo ""
info "Anthropic API Key"
if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  warn "ANTHROPIC_API_KEY is not set in your environment."
  echo "    Get your key at: https://console.anthropic.com/settings/keys"
  echo "    Add this line to your ~/.zshrc or ~/.bash_profile:"
  echo "      export ANTHROPIC_API_KEY=\"sk-ant-...\""
  echo ""
  read -rp "    Paste your Anthropic API key now (or press Enter to skip): " ENTERED_KEY
  if [[ -n "$ENTERED_KEY" ]]; then
    SHELL_RC="$HOME/.zshrc"
    [[ -n "${BASH_VERSION:-}" ]] && SHELL_RC="$HOME/.bash_profile"
    echo "export ANTHROPIC_API_KEY=\"$ENTERED_KEY\"" >> "$SHELL_RC"
    export ANTHROPIC_API_KEY="$ENTERED_KEY"
    success "Key saved to $SHELL_RC"
  else
    warn "Skipped — AI analysis won't work until you set ANTHROPIC_API_KEY."
  fi
else
  success "ANTHROPIC_API_KEY is already set."
fi

# ── 6. Google Calendar OAuth2 ──────────────────────────────────────────────
echo ""
info "Google Calendar Setup"
echo "  To sync Google Calendar, you need OAuth2 client credentials."
echo "  Steps:"
echo "    1. Go to https://console.cloud.google.com/"
echo "    2. Create a project → Enable 'Google Calendar API'"
echo "    3. Create OAuth2 credentials (Desktop app type)"
echo "    4. Download the JSON file"
echo ""
read -rp "  Enter the full path to your downloaded Google credentials JSON (or press Enter to skip): " GCREDS_PATH
if [[ -n "$GCREDS_PATH" && -f "$GCREDS_PATH" ]]; then
  cp "$GCREDS_PATH" "$GOOGLE_CREDS"
  success "Google credentials copied to $GOOGLE_CREDS"
  info "Running first-time Google OAuth flow (browser will open)…"
  "$PYTHON" "$SCRIPT_DIR/sync_calendars.py" --source google || warn "OAuth flow failed — run 'python sync_calendars.py --source google' manually later."
elif [[ -f "$GOOGLE_CREDS" ]]; then
  success "Google credentials already present at $GOOGLE_CREDS"
else
  warn "Skipped Google Calendar setup. You can run it later:"
  echo "       Copy credentials to $GOOGLE_CREDS then run:"
  echo "       python sync_calendars.py --source google"
fi

# ── 7. Notion Token ────────────────────────────────────────────────────────
echo ""
info "Notion Setup"
echo "  To sync Notion, you need an Integration Token."
echo "  Steps:"
echo "    1. Go to https://www.notion.so/my-integrations"
echo "    2. Create a new integration, copy the 'Internal Integration Token'"
echo "    3. In Notion, open your calendar/tasks database → Share → add your integration"
echo "    4. Copy the database ID from the URL:"
echo "       notion.so/<workspace>/<DATABASE_ID>?v=…  ← this part"
echo ""

if [[ -f "$CONFIG_FILE" ]]; then
  success "Config already exists at $CONFIG_FILE"
else
  echo "{}" > "$CONFIG_FILE"
fi

read -rp "  Paste your Notion Integration Token (or press Enter to skip): " NOTION_TOKEN
if [[ -n "$NOTION_TOKEN" ]]; then
  read -rp "  Paste your Notion Calendar Database ID (or press Enter to skip): " NOTION_DB_ID
  "$PYTHON" - <<PYEOF
import json, pathlib
cfg_path = pathlib.Path("$CONFIG_FILE")
cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
cfg["notion_token"] = "$NOTION_TOKEN"
if "$NOTION_DB_ID":
    cfg["notion_calendar_db_id"] = "$NOTION_DB_ID"
cfg_path.write_text(json.dumps(cfg, indent=2))
PYEOF
  success "Notion config saved to $CONFIG_FILE"
  if [[ -n "$NOTION_DB_ID" ]]; then
    info "Running Notion sync test…"
    "$PYTHON" "$SCRIPT_DIR/sync_calendars.py" --source notion || warn "Notion sync failed — check your token and database ID."
  fi
else
  warn "Skipped Notion setup. Edit $CONFIG_FILE later to add notion_token and notion_calendar_db_id."
fi

# ── 8. Generate launchd plist ──────────────────────────────────────────────
echo ""
info "Setting up launchd autostart…"

PYTHON_ABS=$(command -v python3)
LOG_OUT="$SIGNAL_DIR/tracker.log"
LOG_ERR="$SIGNAL_DIR/tracker.error.log"

cat > "$PLIST_SRC" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.signal.tracker</string>

    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON_ABS}</string>
        <string>${TRACKER_PATH}</string>
    </array>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>ThrottleInterval</key>
    <integer>10</integer>

    <key>StandardOutPath</key>
    <string>${LOG_OUT}</string>

    <key>StandardErrorPath</key>
    <string>${LOG_ERR}</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
    </dict>
</dict>
</plist>
PLIST

success "Plist written: $PLIST_SRC"

# ── 9. Register launchd service ────────────────────────────────────────────
cp "$PLIST_SRC" "$PLIST_DEST"
chmod 644 "$PLIST_DEST"

# Unload old version if running
launchctl unload "$PLIST_DEST" 2>/dev/null || true
launchctl load -w "$PLIST_DEST"
success "launchd service registered: com.signal.tracker"

# ── 10. Done ───────────────────────────────────────────────────────────────
echo ""
echo "${GREEN}${BOLD}══════════════════════════════════════════${RESET}"
success "Setup complete!"
echo ""
echo "  Tracker is running in the background and will auto-start on login."
echo "  Logs:      $LOG_OUT"
echo "  Database:  $SIGNAL_DIR/activity.db"
echo ""
echo "  Start the dashboard:"
echo "    ${BOLD}cd $SCRIPT_DIR/dashboard && python app.py${RESET}"
echo "    Then open: http://127.0.0.1:5000"
echo ""
echo "  Sync calendars manually:"
echo "    ${BOLD}python $SCRIPT_DIR/sync_calendars.py${RESET}"
echo ""
echo "  Stop tracker:"
echo "    ${BOLD}launchctl unload $PLIST_DEST${RESET}"
echo "${GREEN}${BOLD}══════════════════════════════════════════${RESET}"
