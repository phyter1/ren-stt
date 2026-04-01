#!/bin/bash
# RenSTT.app launcher — handles first-run setup and launches the client.
# This runs inside the .app bundle as the CFBundleExecutable.

set -euo pipefail

# Resolve paths relative to the .app bundle
APP_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
RESOURCES="$APP_DIR/Contents/Resources"
VENV_DIR="$RESOURCES/.venv"
CONFIG_DIR="$HOME/.config/ren-stt"
CONFIG_FILE="$CONFIG_DIR/config.json"
LOG_DIR="$CONFIG_DIR"

VENV_PYTHON="$VENV_DIR/bin/python3"
VENV_PIP="$VENV_DIR/bin/pip"

HOMEBREW_PREFIX="$(/opt/homebrew/bin/brew --prefix 2>/dev/null || /usr/local/bin/brew --prefix 2>/dev/null || echo "/opt/homebrew")"
export PATH="$VENV_DIR/bin:$HOMEBREW_PREFIX/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# ── First-run setup ─────────────────────────────────────────────────

needs_setup() {
    [[ ! -d "$VENV_DIR" ]] || [[ ! -f "$CONFIG_FILE" ]]
}

show_notification() {
    osascript -e "display notification \"$1\" with title \"Ren STT\""
}

run_setup() {
    show_notification "Setting up Ren STT for the first time..."

    # Check sox
    if ! command -v sox &>/dev/null; then
        osascript -e 'display dialog "Ren STT requires sox for audio recording.\n\nInstall it with:\n  brew install sox\n\nThen relaunch Ren STT." buttons {"OK"} default button "OK" with title "Ren STT" with icon caution'
        exit 1
    fi

    # Create venv
    if [[ ! -d "$VENV_DIR" ]]; then
        python3 -m venv "$VENV_DIR"
        $VENV_PIP install -q -r "$RESOURCES/requirements-client.txt"
    fi

    # Create default config
    if [[ ! -f "$CONFIG_FILE" ]]; then
        mkdir -p "$CONFIG_DIR"
        cat > "$CONFIG_FILE" <<'EOF'
{
  "server": {
    "host": "0.0.0.0",
    "port": 8222
  },
  "client": {
    "server_url": "http://localhost:8222",
    "hotkey": "option+space",
    "mode": "toggle",
    "sensitivity": 18,
    "indicator": true
  }
}
EOF
    fi

    show_notification "Setup complete. Ren STT is running."
}

# ── Configure server URL on first run ───────────────────────────────

prompt_server_url() {
    if [[ -f "$CONFIG_FILE" ]]; then
        return
    fi

    local result
    result=$(osascript -e '
        display dialog "Enter the STT server URL:\n\n• If running the server on THIS machine: use the default\n• If connecting to another machine: enter its address (e.g. macmini.local:8222)" \
            default answer "http://localhost:8222" \
            buttons {"Cancel", "OK"} default button "OK" \
            with title "Ren STT Setup"
        return text returned of result
    ' 2>/dev/null) || exit 0

    if [[ -n "$result" ]]; then
        mkdir -p "$CONFIG_DIR"
        cat > "$CONFIG_FILE" <<EOF
{
  "server": {
    "host": "0.0.0.0",
    "port": 8222
  },
  "client": {
    "server_url": "$result",
    "hotkey": "option+space",
    "mode": "toggle",
    "sensitivity": 18,
    "indicator": true
  }
}
EOF
    fi
}

# ── Register as Login Item ──────────────────────────────────────────

register_login_item() {
    # Add to Login Items so it starts on boot
    osascript -e "
        tell application \"System Events\"
            if not (exists login item \"RenSTT\") then
                make login item at end with properties {path:\"$APP_DIR\", hidden:true}
            end if
        end tell
    " 2>/dev/null || true
}

# ── Main ────────────────────────────────────────────────────────────

if needs_setup; then
    prompt_server_url
    run_setup
fi

register_login_item

# Launch the client
exec "$VENV_PYTHON" -u "$RESOURCES/stt-cli.py" \
    >> "$LOG_DIR/client.log" 2>&1
