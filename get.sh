#!/bin/bash
set -euo pipefail

# ren-stt one-line installer
# curl -fsSL https://raw.githubusercontent.com/phyter1/ren-stt/main/get.sh | bash
#
# Or with a mode pre-selected:
# curl -fsSL https://raw.githubusercontent.com/phyter1/ren-stt/main/get.sh | bash -s -- standalone
# curl -fsSL https://raw.githubusercontent.com/phyter1/ren-stt/main/get.sh | bash -s -- server
# curl -fsSL https://raw.githubusercontent.com/phyter1/ren-stt/main/get.sh | bash -s -- client --server myhost.local

GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${GREEN}▸${NC} $1"; }
warn()  { echo -e "${YELLOW}▸${NC} $1"; }
error() { echo -e "${RED}▸${NC} $1"; exit 1; }

INSTALL_DIR="$HOME/.local/share/ren-stt"
REPO="https://github.com/phyter1/ren-stt.git"

echo ""
echo -e "${BOLD}  ren-stt${NC} — local speech-to-text for macOS"
echo ""

# ── Check prerequisites ────────────────────────────────────────────

if [[ "$(uname)" != "Darwin" ]]; then
    error "ren-stt only works on macOS"
fi

if ! command -v python3 &>/dev/null; then
    error "Python 3 is required. Install from https://python.org"
fi

if ! command -v git &>/dev/null; then
    error "git is required. Install Xcode Command Line Tools: xcode-select --install"
fi

# ── Clone or update ────────────────────────────────────────────────

if [[ -d "$INSTALL_DIR" ]]; then
    info "Updating existing install..."
    cd "$INSTALL_DIR" && git pull -q
else
    info "Installing to $INSTALL_DIR..."
    git clone -q "$REPO" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# ── Pick mode ──────────────────────────────────────────────────────

MODE="${1:-}"
shift 2>/dev/null || true

if [[ -z "$MODE" ]]; then
    echo "  How would you like to use this machine?"
    echo ""
    echo "    1) ${BOLD}standalone${NC}  — Server + client on this machine (Apple Silicon)"
    echo "    2) ${BOLD}server${NC}      — Just the inference server (Apple Silicon)"
    echo "    3) ${BOLD}client${NC}      — Just the hotkey client (connects to a server)"
    echo ""
    read -rp "  Choose [1/2/3]: " choice
    case "$choice" in
        1|standalone) MODE="standalone" ;;
        2|server)     MODE="server" ;;
        3|client)     MODE="client" ;;
        *)            error "Invalid choice" ;;
    esac
fi

# ── Client needs server address ────────────────────────────────────

SERVER_ARG=""
if [[ "$MODE" == "client" ]]; then
    # Check if --server was passed
    for arg in "$@"; do
        if [[ "$arg" == --server ]]; then
            SERVER_ARG="found"
        fi
    done

    if [[ -z "$SERVER_ARG" ]]; then
        echo ""
        read -rp "  Server address (e.g. macmini.local): " server_host
        if [[ -z "$server_host" ]]; then
            error "Server address is required for client mode"
        fi
        set -- --server "$server_host" "$@"
    fi
fi

# ── Run install.sh ─────────────────────────────────────────────────

echo ""
exec ./install.sh "$MODE" "$@"
