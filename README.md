# ren-stt

Local speech-to-text for macOS. A Superwhisper replacement that runs entirely on your hardware.

**Server:** Parakeet TDT 0.6B on MLX — fast, private, no cloud API needed.  
**Client:** Global hotkey, floating waveform indicator, menu bar icon, auto-paste.

Runs on one machine or split across a network: server on your Apple Silicon box, client on any Mac.

## Quick Start

### Client (any Mac) — DMG installer

```bash
git clone https://github.com/phyter1/ren-stt.git
cd ren-stt
./build-dmg.sh
open dist/RenSTT.dmg
```

Drag **RenSTT** to **Applications**, then open it. First launch:
- Prompts for STT server URL (localhost or a remote machine)
- Creates a virtual environment and installs dependencies
- macOS asks for **Accessibility** and **Microphone** permissions (native dialogs)
- Registers as a Login Item (starts on boot)

### Server (Apple Silicon)

```bash
git clone https://github.com/phyter1/ren-stt.git
cd ren-stt
./install.sh server
```

### CLI install (alternative)

```bash
# All-in-one on Apple Silicon:
./install.sh standalone

# Client only, pointing at a remote server:
./install.sh client --server your-server.local
```

## Usage

| Action | Key |
|--------|-----|
| Start/stop recording | **Option+Space** |
| Cancel recording | **Escape** |

Text is transcribed and pasted into whatever app has focus.

## Install Modes

| Mode | What it installs | Requires |
|------|-----------------|----------|
| `standalone` | Server + client | Apple Silicon, sox |
| `server` | Inference server only | Apple Silicon |
| `client` | Hotkey client only | sox, network access to server |

All three modes install as **launchd services** that start on boot.

## Configuration

Config lives at `~/.config/ren-stt/config.json`:

```json
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
```

- **hotkey**: Any combo of `option`, `cmd`, `ctrl`, `shift` + a trigger key (`space`, `f1`-`f12`, or a letter)
- **mode**: `toggle` (press to start/stop) or `push-to-talk` (hold to record)
- **sensitivity**: Audio level bar sensitivity (higher = more responsive, default 18)
- **indicator**: Show/hide the floating waveform window

## CLI Options

```bash
# Client
python3 stt-cli.py --hotkey ctrl+shift+space  # custom hotkey
python3 stt-cli.py --push-to-talk              # hold-to-record mode
python3 stt-cli.py --server http://host:8222   # remote server
python3 stt-cli.py --no-indicator              # no waveform popup
python3 stt-cli.py --sensitivity 25            # boost for quiet mics

# Server
python3 stt-server.py --port 9000             # custom port
```

## API

The server exposes a simple HTTP API:

```bash
# Transcribe audio
curl -X POST http://localhost:8222/transcribe -F audio=@recording.wav

# Health check
curl http://localhost:8222/health

# Web UI (browser mic recording + file upload)
open http://localhost:8222
```

Response:
```json
{
  "text": "transcribed text here",
  "duration_s": 4.06,
  "inference_ms": 1170,
  "rtf": 3.5
}
```

## Architecture

```
┌─────────────────────────────────┐
│  Any Mac (client)               │
│  ┌───────────┐  ┌────────────┐  │
│  │ stt-cli   │  │ menu bar   │  │
│  │ (hotkey)  │  │ (waveform) │  │
│  └─────┬─────┘  └────────────┘  │
│        │ audio                   │
│        ▼                         │
│  ┌───────────┐                   │
│  │    sox     │                   │
│  │ (record)  │                   │
│  └─────┬─────┘                   │
└────────┼────────────────────────┘
         │ HTTP POST /transcribe
         ▼
┌─────────────────────────────────┐
│  Apple Silicon (server)         │
│  ┌───────────┐  ┌────────────┐  │
│  │ stt-server│  │  Parakeet  │  │
│  │ (HTTP)    │──│  (MLX)     │  │
│  └───────────┘  └────────────┘  │
└─────────────────────────────────┘
```

## Performance

Tested on M1 Pro (16GB):

| Audio length | Inference time | Speed |
|-------------|---------------|-------|
| 4s          | ~1.2s         | 3.5x realtime |
| 16s         | ~500ms        | 31x realtime |

Longer audio is proportionally faster due to MLX batch processing.

## Requirements

- **Server:** macOS with Apple Silicon, Python 3.10+
- **Client:** macOS (any Mac), Python 3.10+, sox (`brew install sox`)
- **Model:** ~400MB downloaded on first server start

The installer creates a virtual environment at `.venv/` inside the repo. All Python dependencies are isolated there — nothing is installed globally.

## Uninstall

```bash
./install.sh uninstall        # remove launchd services
rm -rf ~/.config/ren-stt      # remove config
rm -rf .venv                  # remove virtual environment
```

## License

MIT
