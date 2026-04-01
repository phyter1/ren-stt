# ren-stt

Local speech-to-text for macOS. Runs on your hardware — no cloud, no subscription.

Press **Option+Space**, speak, press again. Text appears wherever your cursor is.

Powered by [Parakeet TDT](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2) on [MLX](https://github.com/ml-explore/mlx) with automatic punctuation, capitalization, and filler word removal. 30x realtime on Apple Silicon.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/phyter1/ren-stt/main/get.sh | bash
```

The installer asks you to pick a mode:

| Mode | What it does | Requires |
|------|-------------|----------|
| **standalone** | Runs the model + hotkey client on one machine | Apple Silicon |
| **server** | Hosts the model for other machines on your network | Apple Silicon |
| **client** | Hotkey client that connects to a server | Any Mac |

Or pass the mode directly:

```bash
# Standalone (Apple Silicon)
curl -fsSL https://raw.githubusercontent.com/phyter1/ren-stt/main/get.sh | bash -s -- standalone

# Server only
curl -fsSL https://raw.githubusercontent.com/phyter1/ren-stt/main/get.sh | bash -s -- server

# Client pointing at a server
curl -fsSL https://raw.githubusercontent.com/phyter1/ren-stt/main/get.sh | bash -s -- client --server myhost.local
```

### What the installer does

1. Clones the repo to `~/.local/share/ren-stt`
2. Creates a Python virtual environment (nothing installed globally)
3. Installs dependencies for your chosen mode
4. Writes config to `~/.config/ren-stt/config.json`
5. Starts a launchd service (runs on boot)
6. Walks you through granting Accessibility + Microphone permissions

### macOS app (Apple Silicon only)

If you prefer a drag-to-install `.app`:

```bash
git clone https://github.com/phyter1/ren-stt.git && cd ren-stt
./build-dmg.sh
open dist/RenSTT.dmg
```

Or download `RenSTT.dmg` from the [latest release](https://github.com/phyter1/ren-stt/releases).

## Usage

| Action | Key |
|--------|-----|
| Start/stop recording | **Option+Space** |
| Cancel recording | **Escape** |

Transcribed text is copied to clipboard and pasted into the active app.

A waveform icon appears in the menu bar — black when idle, red when recording, orange when transcribing.

## Configuration

Edit `~/.config/ren-stt/config.json`:

```json
{
  "client": {
    "server_url": "http://localhost:8222",
    "hotkey": "option+space",
    "mode": "toggle",
    "sensitivity": 18
  }
}
```

| Option | Values | Default |
|--------|--------|---------|
| `hotkey` | Any combo: `option`, `cmd`, `ctrl`, `shift` + `space`, `f1`-`f12`, or a letter | `option+space` |
| `mode` | `toggle` (press to start/stop) or `push-to-talk` (hold to record) | `toggle` |
| `sensitivity` | Audio level bar responsiveness (higher = more sensitive) | `18` |
| `server_url` | URL of the STT server | `http://localhost:8222` |

Server config:

| Option | Values | Default |
|--------|--------|---------|
| `model` | `small` (0.6B), `large` (1.1B), or any HuggingFace model ID | `small` |
| `port` | Server port | `8222` |
| `host` | Bind address | `0.0.0.0` |

## Network setup

Run the server on one Apple Silicon machine, clients on everything else:

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  MacBook Air │     │   Mac Mini   │     │   Mac Pro    │
│   (client)   │────▶│  (server)    │◀────│   (client)   │
│  Option+Space│     │  Parakeet    │     │  Option+Space│
└──────────────┘     │  MLX :8222   │     └──────────────┘
                     └──────────────┘
```

Clients send audio over HTTP, server returns text. All on your local network.

## API

The server has a simple HTTP API:

```bash
# Transcribe audio
curl -X POST http://localhost:8222/transcribe -F audio=@recording.wav
# {"text": "hello world", "duration_s": 2.1, "inference_ms": 380, "rtf": 5.5}

# Health check
curl http://localhost:8222/health

# Web UI (browser mic + file upload)
open http://localhost:8222
```

## Post-processing

Transcriptions are automatically cleaned:

1. **Filler removal** — strips "uh", "um", "hmm", "er" (0ms, regex)
2. **Punctuation & capitalization** — commas, periods, question marks, proper caps via [punct_cap_seg](https://huggingface.co/1-800-BAD-CODE/punct_cap_seg_47_language) ONNX model (~65ms, runs on CPU alongside MLX)
3. **Sentence segmentation** — splits run-on speech into sentences

Regex fallback if the punctuation model isn't available.

## Performance

Tested on M1 Pro (16GB) with Parakeet 1.1B + punctuation model:

| Audio | Total latency | Pipeline |
|-------|--------------|----------|
| 2s | ~250ms | STT ~200ms + punct ~50ms |
| 6s | ~300ms | STT ~240ms + punct ~60ms |
| 16s | ~530ms | STT ~460ms + punct ~70ms |
| 47s | ~1.9s | STT ~1.8s + punct ~70ms |

## Uninstall

```bash
# Via installer
cd ~/.local/share/ren-stt && ./install.sh uninstall

# Full removal
rm -rf ~/.local/share/ren-stt ~/.config/ren-stt
```

## License

MIT
