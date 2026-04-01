#!/bin/bash
# RenSTT.app launcher — first-run mode picker, setup, and launch.
# This runs inside the .app bundle as the CFBundleExecutable.

set -euo pipefail

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

mkdir -p "$CONFIG_DIR"

# ── Helpers ─────────────────────────────────────────────────────────

notify() {
    osascript -e "display notification \"$1\" with title \"Ren STT\"" 2>/dev/null || true
}

dialog() {
    osascript -e "$1" 2>/dev/null
}

get_mode() {
    # Read install mode from config
    if [[ -f "$CONFIG_FILE" ]]; then
        "$VENV_PYTHON" -c "import json; print(json.load(open('$CONFIG_FILE')).get('install_mode',''))" 2>/dev/null || echo ""
    else
        echo ""
    fi
}

is_apple_silicon() {
    [[ "$(uname -m)" == "arm64" ]]
}

# ── First-run: mode picker ──────────────────────────────────────────

pick_mode() {
    local choice
    choice=$(dialog '
        display dialog "Welcome to Ren STT!\n\nHow would you like to use this machine?\n\n• Client — Hotkey + transcription (connects to a server)\n• Server — Hosts the speech-to-text model (Apple Silicon only)\n• Standalone — Both server + client on this machine" \
            buttons {"Client", "Server", "Standalone"} \
            default button "Standalone" \
            with title "Ren STT Setup"
        return button returned of result
    ') || exit 0

    case "$choice" in
        Client)     echo "client" ;;
        Server)     echo "server" ;;
        Standalone) echo "standalone" ;;
    esac
}

prompt_server_url() {
    local result
    result=$(dialog '
        display dialog "Enter the STT server address:\n\n(e.g. macmini.local or 192.168.1.50)" \
            default answer "" \
            buttons {"Cancel", "OK"} default button "OK" \
            with title "Ren STT — Server Address"
        return text returned of result
    ') || exit 0

    # Normalize to URL
    if [[ -n "$result" ]]; then
        if [[ "$result" != http* ]]; then
            result="http://$result"
        fi
        if [[ ! "$result" =~ :[0-9]+$ ]]; then
            result="$result:8222"
        fi
        echo "$result"
    else
        echo "http://localhost:8222"
    fi
}

# ── Setup ───────────────────────────────────────────────────────────

write_config() {
    local mode="$1"
    local server_url="$2"
    cat > "$CONFIG_FILE" <<EOF
{
  "install_mode": "$mode",
  "server": {
    "host": "0.0.0.0",
    "port": 8222
  },
  "client": {
    "server_url": "$server_url",
    "hotkey": "option+space",
    "mode": "toggle",
    "sensitivity": 18,
    "indicator": true
  }
}
EOF
}

setup_venv() {
    if [[ ! -d "$VENV_DIR" ]]; then
        notify "Creating virtual environment..."
        python3 -m venv "$VENV_DIR"
    fi
}

install_client_deps() {
    # Check sox
    if ! command -v sox &>/dev/null; then
        dialog 'display dialog "Ren STT requires sox for audio recording.\n\nInstall it with:\n  brew install sox\n\nThen relaunch Ren STT." buttons {"OK"} default button "OK" with title "Ren STT" with icon caution'
        exit 1
    fi
    notify "Installing client dependencies..."
    $VENV_PIP install -q -r "$RESOURCES/requirements-client.txt"
}

install_server_deps() {
    if ! is_apple_silicon; then
        dialog 'display dialog "Server mode requires Apple Silicon (M1/M2/M3).\n\nThis machine is not Apple Silicon. Choose Client mode instead." buttons {"OK"} default button "OK" with title "Ren STT" with icon stop'
        # Reset config and re-prompt
        rm -f "$CONFIG_FILE"
        exec "$0"
    fi
    notify "Installing server dependencies (this may take a minute)..."
    $VENV_PIP install -q -r "$RESOURCES/requirements-server.txt"
}

run_first_setup() {
    local mode
    mode=$(pick_mode)

    local server_url="http://localhost:8222"

    case "$mode" in
        client)
            server_url=$(prompt_server_url)
            write_config "$mode" "$server_url"
            setup_venv
            install_client_deps
            ;;
        server)
            write_config "$mode" "$server_url"
            setup_venv
            install_server_deps
            ;;
        standalone)
            write_config "$mode" "$server_url"
            setup_venv
            install_server_deps
            install_client_deps
            ;;
    esac

    notify "Setup complete. Ren STT is running."
}

# ── Register as Login Item ──────────────────────────────────────────

register_login_item() {
    osascript -e "
        tell application \"System Events\"
            if not (exists login item \"RenSTT\") then
                make login item at end with properties {path:\"$APP_DIR\", hidden:true}
            end if
        end tell
    " 2>/dev/null || true
}

# ── Launch ──────────────────────────────────────────────────────────

launch_server() {
    "$VENV_PYTHON" -u "$RESOURCES/stt-server.py" \
        >> "$LOG_DIR/server.log" 2>&1 &
    SERVER_PID=$!
    echo "$SERVER_PID" > "$CONFIG_DIR/server.pid"
}

launch_client() {
    "$VENV_PYTHON" -u "$RESOURCES/stt-cli.py" \
        >> "$LOG_DIR/client.log" 2>&1
}

launch_server_only() {
    "$VENV_PYTHON" -u "$RESOURCES/stt-server.py" \
        >> "$LOG_DIR/server.log" 2>&1
}

# ── Main ────────────────────────────────────────────────────────────

# First-run setup
if [[ ! -f "$CONFIG_FILE" ]] || [[ ! -d "$VENV_DIR" ]]; then
    run_first_setup
fi

register_login_item

MODE=$(get_mode)

QUIT_FLAG="$CONFIG_DIR/.quit"

# Clean quit flag on launch (we're starting fresh)
rm -f "$QUIT_FLAG"

# If we receive SIGTERM/SIGINT (user quit), set the flag so we don't retry
trap 'touch "$QUIT_FLAG"; exit 0' SIGTERM SIGINT

run_with_backoff() {
    # If the process exits quickly (e.g. permissions not granted), wait before retrying.
    # After 3 fast failures, show a dialog and stop.
    # If the user explicitly quit, don't retry.
    local failures=0
    while true; do
        local start_time
        start_time=$(date +%s)

        "$@"
        local exit_code=$?

        # User quit — don't retry
        if [[ -f "$QUIT_FLAG" ]]; then
            rm -f "$QUIT_FLAG"
            break
        fi

        local elapsed=$(( $(date +%s) - start_time ))

        if [[ $exit_code -eq 0 ]]; then
            break  # clean exit
        fi

        if [[ $elapsed -lt 5 ]]; then
            failures=$((failures + 1))
            if [[ $failures -ge 3 ]]; then
                dialog 'display dialog "Ren STT failed to start.\n\nCheck Accessibility permissions in System Settings > Privacy & Security > Accessibility.\n\nLogs: ~/.config/ren-stt/" buttons {"Open Settings", "OK"} default button "OK" with title "Ren STT" with icon caution'
                open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
                exit 1
            fi
            sleep 5
        else
            failures=0  # ran long enough — reset
        fi
    done
}

case "$MODE" in
    client)
        run_with_backoff launch_client
        ;;
    server)
        run_with_backoff launch_server_only
        ;;
    standalone)
        launch_server
        sleep 3  # let server load before client tries to connect
        run_with_backoff launch_client
        ;;
    *)
        # Config exists but no mode — re-run setup
        rm -f "$CONFIG_FILE"
        exec "$0"
        ;;
esac
