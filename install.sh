#!/bin/bash
set -euo pipefail

# ren-stt installer
# Usage:
#   ./install.sh server                          # inference server only (Apple Silicon + MLX)
#   ./install.sh client --server myhost.local    # client only (connects to remote server)
#   ./install.sh standalone                      # both server + client on one machine
#   ./install.sh uninstall                       # remove services and config

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_DIR="$HOME/.config/ren-stt"
PLIST_DIR="$HOME/Library/LaunchAgents"
PYTHON="${PYTHON:-python3}"

SERVER_LABEL="com.ren-stt.server"
CLIENT_LABEL="com.ren-stt.client"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}▸${NC} $1"; }
warn()  { echo -e "${YELLOW}▸${NC} $1"; }
error() { echo -e "${RED}▸${NC} $1"; exit 1; }

# ── Helpers ─────────────────────────────────────────────────────────

check_python() {
    if ! command -v "$PYTHON" &>/dev/null; then
        error "Python 3 not found. Install it or set PYTHON env var."
    fi
    info "Using Python: $($PYTHON --version)"
}

check_sox() {
    if ! command -v sox &>/dev/null; then
        warn "sox not found — installing via Homebrew..."
        if command -v brew &>/dev/null; then
            brew install sox
        else
            error "sox is required for audio recording. Install it: brew install sox"
        fi
    fi
    info "sox: $(command -v sox)"
}

check_apple_silicon() {
    if [[ "$(uname -m)" != "arm64" ]]; then
        error "Server mode requires Apple Silicon (MLX). This machine is $(uname -m)."
    fi
    info "Apple Silicon detected"
}

write_config() {
    local server_url="$1"
    mkdir -p "$CONFIG_DIR"

    if [[ -f "$CONFIG_DIR/config.json" ]]; then
        info "Config already exists at $CONFIG_DIR/config.json — preserving it"
        return
    fi

    local port=8222
    cat > "$CONFIG_DIR/config.json" <<EOF
{
  "server": {
    "host": "0.0.0.0",
    "port": $port
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
    info "Config written to $CONFIG_DIR/config.json"
}

install_server_plist() {
    local plist="$PLIST_DIR/$SERVER_LABEL.plist"
    cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$SERVER_LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>$REPO_DIR/stt-server.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$REPO_DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$HOME/.config/ren-stt/server.log</string>
    <key>StandardErrorPath</key>
    <string>$HOME/.config/ren-stt/server.log</string>
</dict>
</plist>
EOF
    launchctl unload "$plist" 2>/dev/null || true
    launchctl load "$plist"
    info "Server service installed and started ($SERVER_LABEL)"
}

install_client_plist() {
    local plist="$PLIST_DIR/$CLIENT_LABEL.plist"
    cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$CLIENT_LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>-u</string>
        <string>$REPO_DIR/stt-cli.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$REPO_DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$HOME/.config/ren-stt/client.log</string>
    <key>StandardErrorPath</key>
    <string>$HOME/.config/ren-stt/client.log</string>
</dict>
</plist>
EOF
    launchctl unload "$plist" 2>/dev/null || true
    launchctl load "$plist"
    info "Client service installed and started ($CLIENT_LABEL)"
}

uninstall() {
    info "Uninstalling ren-stt services..."

    for label in "$SERVER_LABEL" "$CLIENT_LABEL"; do
        local plist="$PLIST_DIR/$label.plist"
        if [[ -f "$plist" ]]; then
            launchctl unload "$plist" 2>/dev/null || true
            rm "$plist"
            info "Removed $label"
        fi
    done

    echo ""
    info "Services removed. Config preserved at $CONFIG_DIR/config.json"
    info "To fully remove: rm -rf $CONFIG_DIR"
}

# ── Main ────────────────────────────────────────────────────────────

usage() {
    echo "Usage: $0 <mode> [options]"
    echo ""
    echo "Modes:"
    echo "  server                         Install inference server (Apple Silicon only)"
    echo "  client --server <host:port>    Install hotkey client (connects to remote server)"
    echo "  standalone                     Install both server + client on this machine"
    echo "  uninstall                      Remove launchd services"
    echo ""
    echo "Options:"
    echo "  --server <url>    Server URL for client mode (e.g. myhost.local:8222)"
    echo "  --port <port>     Server port (default: 8222)"
    echo ""
    echo "Examples:"
    echo "  ./install.sh standalone"
    echo "  ./install.sh server"
    echo "  ./install.sh client --server macmini.local"
    echo "  ./install.sh uninstall"
    exit 1
}

MODE="${1:-}"
shift || true

if [[ -z "$MODE" ]]; then
    usage
fi

# Parse optional args
SERVER_HOST=""
SERVER_PORT="8222"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --server) SERVER_HOST="$2"; shift 2 ;;
        --port)   SERVER_PORT="$2"; shift 2 ;;
        *)        error "Unknown option: $1" ;;
    esac
done

case "$MODE" in
    server)
        echo "=== Installing ren-stt server ==="
        check_python
        check_apple_silicon
        info "Installing Python dependencies..."
        $PYTHON -m pip install -q -r "$REPO_DIR/requirements-server.txt"
        write_config "http://localhost:$SERVER_PORT"
        mkdir -p "$PLIST_DIR"
        install_server_plist
        echo ""
        info "Server running on port $SERVER_PORT"
        info "Test: curl http://localhost:$SERVER_PORT/health"
        info "Web UI: http://localhost:$SERVER_PORT"
        ;;

    client)
        if [[ -z "$SERVER_HOST" ]]; then
            error "Client mode requires --server <host>. Example: ./install.sh client --server macmini.local"
        fi
        # Build URL from host — add port if not specified
        if [[ "$SERVER_HOST" != http* ]]; then
            SERVER_HOST="http://$SERVER_HOST"
        fi
        if [[ "$SERVER_HOST" != *:* ]] || [[ "$SERVER_HOST" =~ ^http://[^:]+$ ]]; then
            SERVER_HOST="$SERVER_HOST:$SERVER_PORT"
        fi

        echo "=== Installing ren-stt client ==="
        check_python
        check_sox
        info "Installing Python dependencies..."
        $PYTHON -m pip install -q -r "$REPO_DIR/requirements-client.txt"
        write_config "$SERVER_HOST"
        mkdir -p "$PLIST_DIR"
        install_client_plist
        echo ""
        info "Client running — server at $SERVER_HOST"
        info "Hotkey: Option+Space (configure in $CONFIG_DIR/config.json)"
        warn "Grant Accessibility permissions to your terminal app in System Settings > Privacy & Security"
        ;;

    standalone)
        echo "=== Installing ren-stt (standalone) ==="
        check_python
        check_apple_silicon
        check_sox
        info "Installing all Python dependencies..."
        $PYTHON -m pip install -q -r "$REPO_DIR/requirements-server.txt"
        $PYTHON -m pip install -q -r "$REPO_DIR/requirements-client.txt"
        write_config "http://localhost:$SERVER_PORT"
        mkdir -p "$PLIST_DIR"
        install_server_plist
        sleep 3  # let server load the model before starting client
        install_client_plist
        echo ""
        info "Server running on port $SERVER_PORT"
        info "Client running — hotkey: Option+Space"
        warn "Grant Accessibility permissions to your terminal app in System Settings > Privacy & Security"
        ;;

    uninstall)
        uninstall
        ;;

    *)
        usage
        ;;
esac

echo ""
info "Done."
