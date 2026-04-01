#!/usr/bin/env python3
"""
Speech-to-text server powered by Parakeet TDT 0.6B on MLX (Apple Silicon).
Serves transcription over HTTP — accessible from any machine on the network.

Usage:
  python3 stt-server.py                    # start on port 8222
  python3 stt-server.py --port 9000        # custom port

API:
  POST /transcribe
    - multipart/form-data with 'audio' file field
    - or raw audio bytes in body with Content-Type: audio/*
    - Returns JSON: {"text": "...", "segments": [...], "duration_s": ..., "inference_ms": ...}

  GET /health
    - Returns {"status": "ok", "model": "...", "ready": true}

  GET /
    - Web UI for testing (record from browser mic or upload a file)

From another machine:
  curl -X POST http://<this-machine>.local:8222/transcribe -F audio=@recording.wav
"""

import json
import os
import sys
import time
import tempfile
import argparse
import io
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs

MODELS = {
    "small": "mlx-community/parakeet-tdt-0.6b-v3",
    "large": "mlx-community/parakeet-tdt-1.1b",
    # Aliases
    "0.6b": "mlx-community/parakeet-tdt-0.6b-v3",
    "1.1b": "mlx-community/parakeet-tdt-1.1b",
}
DEFAULT_MODEL = "small"

import re
import numpy as np
import librosa
import wave

# ── Punctuation model ──────────────────────────────────────────────

_punct_model = None


def load_punctuation_model():
    """Load the ONNX punctuation/capitalization/segmentation model."""
    global _punct_model
    try:
        from punctuators.models.punc_cap_seg_model import PunctCapSegConfigONNX, PunctCapSegModelONNX
        from huggingface_hub import snapshot_download
        import warnings
        warnings.filterwarnings("ignore", message="Specified provider.*CUDA")

        print("Loading punctuation model...", flush=True)
        path = snapshot_download("1-800-BAD-CODE/punct_cap_seg_47_language")
        cfg = PunctCapSegConfigONNX(
            directory=path,
            spe_filename="spe_unigram_64k_lowercase_47lang.model",
            model_filename="punct_cap_seg_47lang.onnx",
            config_filename="config.yaml",
        )
        _punct_model = PunctCapSegModelONNX(cfg)
        print("Punctuation model ready.", flush=True)
    except Exception as e:
        print(f"Punctuation model not available ({e}). Using regex fallback.", flush=True)


def clean_transcript(text):
    """Clean transcription: remove fillers, add punctuation + capitalization."""
    if not text:
        return text

    # Step 1: Remove filler words
    fillers = [
        r"\b(uh|uhh|uhm|um|umm|hmm|hm|er)\b",
        r"\b(uh|um)'s\b",
    ]
    for f in fillers:
        text = re.sub(f, "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()

    if not text:
        return text

    # Step 2: Punctuation model (if available)
    if _punct_model is not None:
        try:
            results = _punct_model.infer([text.lower()])
            if results and results[0]:
                return " ".join(results[0])
        except Exception:
            pass  # fall through to regex

    # Step 3: Regex fallback
    text = re.sub(r"\s+([.,!?])", r"\1", text)
    text = re.sub(r"([.,!?])\1+", r"\1", text)
    if text:
        text = text[0].upper() + text[1:]
        text = re.sub(r"([.!?]\s+)(\w)", lambda m: m.group(1) + m.group(2).upper(), text)
    text = re.sub(r"\bi\b", "I", text)
    if text:
        text = text[0].upper() + text[1:]
    if text and text[-1] not in ".!?":
        text += "."
    q_words = r"^(what|how|where|when|why|who|whom|whose|which|does|do|did|is|are|was|were|can|could|would|will|shall|should|has|have|had|may|might)\b"
    if re.match(q_words, text, re.IGNORECASE) and text.endswith("."):
        text = text[:-1] + "?"
    return text

# Globals — set during init
MODEL_NAME = None
model = None
load_time = 0


def load_model(model_key):
    """Load a Parakeet model by key or full HuggingFace ID."""
    global MODEL_NAME, model, load_time

    if model_key in MODELS:
        MODEL_NAME = MODELS[model_key]
    else:
        MODEL_NAME = model_key  # assume it's a full HF model ID

    print(f"Loading {MODEL_NAME}...", flush=True)
    load_start = time.time()

    import mlx.core as mx
    from parakeet_mlx import from_pretrained

    # Pin model weights in wired memory so macOS can't page them out
    # when other MLX processes (fleet router, etc.) compete for GPU memory
    try:
        mx.metal.set_wired_limit(6 * 1024**3)  # 6GB — enough for 1.1B + overhead
        print("Wired memory limit set (6GB).", flush=True)
    except Exception as e:
        print(f"Could not set wired limit: {e}", flush=True)

    model = from_pretrained(MODEL_NAME)
    load_time = time.time() - load_start
    print(f"Model loaded in {load_time:.1f}s", flush=True)

    # Pre-warm
    print("Warming up...", flush=True)
    dummy_path = os.path.join(tempfile.gettempdir(), "stt_warmup.wav")
    w = wave.open(dummy_path, 'w')
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(16000)
    w.writeframes(b'\x00\x00' * 16000)
    w.close()
    try:
        model.transcribe(dummy_path)
    except Exception as e:
        print(f"Warmup note: {e}", flush=True)
    print("Ready.", flush=True)

    # Load punctuation model alongside STT
    load_punctuation_model()


class STTHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self._respond(200, {
                "status": "ok",
                "model": MODEL_NAME,
                "ready": True,
                "load_time_s": round(load_time, 1),
            })
        elif self.path == '/':
            self._respond_html()
        else:
            self._respond(404, {"error": "not found"})

    def do_POST(self):
        if self.path != '/transcribe':
            self._respond(404, {"error": "not found"})
            return

        content_type = self.headers.get('Content-Type', '')
        content_length = int(self.headers.get('Content-Length', 0))

        if content_length == 0:
            self._respond(400, {"error": "no audio data"})
            return

        # Read the body
        body = self.rfile.read(content_length)

        # Extract audio file from multipart or raw body
        audio_data = None
        filename = "audio.wav"

        if 'multipart/form-data' in content_type:
            audio_data, filename = self._parse_multipart(body, content_type)
        else:
            audio_data = body

        if not audio_data:
            self._respond(400, {"error": "could not parse audio"})
            return

        # Write to temp file
        suffix = os.path.splitext(filename)[1] or '.wav'
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(audio_data)
            temp_path = f.name

        try:
            # Transcribe
            start = time.time()
            audio_np, sr = librosa.load(temp_path, sr=16000, mono=True)
            result = model.transcribe(temp_path)
            inference_ms = int((time.time() - start) * 1000)

            audio_duration = len(audio_np) / 16000

            # Parse result — can be AlignedResult, list, dict, or string
            text = ""
            segments = []
            if hasattr(result, 'text'):
                text = result.text
            elif hasattr(result, 'sentences'):
                text = ' '.join(s.text for s in result.sentences if hasattr(s, 'text'))
                segments = [{"text": s.text, "start": getattr(s, 'start', 0), "end": getattr(s, 'end', 0)}
                           for s in result.sentences if hasattr(s, 'text')]
            elif isinstance(result, list):
                text = ' '.join(str(s) for s in result).strip()
            elif isinstance(result, dict):
                text = result.get('text', str(result))
            else:
                text = str(result)

            rtf = round(audio_duration / (inference_ms / 1000), 1) if inference_ms > 0 else 0

            text = clean_transcript(text.strip())

            self._respond(200, {
                "text": text,
                "segments": segments,
                "duration_s": round(audio_duration, 2),
                "inference_ms": inference_ms,
                "rtf": rtf,
            })

            self.log_message(f"Transcribed {audio_duration:.1f}s audio in {inference_ms}ms ({rtf}x realtime)")

        except Exception as e:
            self._respond(500, {"error": str(e)})
        finally:
            os.unlink(temp_path)

    def _parse_multipart(self, body, content_type):
        """Extract file from multipart form data."""
        import re
        boundary_match = re.search(r'boundary=(.+?)(?:;|$)', content_type)
        if not boundary_match:
            return None, None

        boundary = boundary_match.group(1).strip()
        if boundary.startswith('"') and boundary.endswith('"'):
            boundary = boundary[1:-1]

        parts = body.split(f'--{boundary}'.encode())
        for part in parts:
            if b'Content-Disposition' in part and b'filename=' in part:
                # Extract filename
                header_end = part.find(b'\r\n\r\n')
                if header_end == -1:
                    continue
                headers = part[:header_end].decode('utf-8', errors='replace')
                fname_match = re.search(r'filename="(.+?)"', headers)
                filename = fname_match.group(1) if fname_match else "audio.wav"

                file_data = part[header_end + 4:]
                # Strip trailing \r\n
                if file_data.endswith(b'\r\n'):
                    file_data = file_data[:-2]
                return file_data, filename

        return None, None

    def _respond(self, code, data):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())

    def _respond_html(self):
        """Simple web UI for testing."""
        html = '''<!DOCTYPE html>
<html><head><title>Ren STT</title>
<style>
  body { font-family: -apple-system, sans-serif; background: #0a0a0a; color: #e5e5e5;
         max-width: 600px; margin: 40px auto; padding: 20px; }
  h1 { font-size: 20px; margin-bottom: 20px; }
  .recorder { text-align: center; padding: 40px; }
  button { background: #dc2626; color: white; border: none; padding: 16px 32px;
           border-radius: 50%; font-size: 18px; cursor: pointer; width: 80px; height: 80px; }
  button:hover { background: #ef4444; }
  button.recording { background: #16a34a; animation: pulse 1s infinite; }
  @keyframes pulse { 0%,100% { transform: scale(1); } 50% { transform: scale(1.1); } }
  #result { margin-top: 24px; padding: 16px; background: #141414; border-radius: 8px;
            min-height: 60px; white-space: pre-wrap; line-height: 1.6; }
  #status { margin-top: 12px; font-size: 13px; color: #737373; }
  .upload { margin-top: 20px; padding: 16px; background: #141414; border-radius: 8px; }
  input[type=file] { margin-bottom: 10px; }
</style></head><body>
<h1>Ren Speech-to-Text</h1>
<div class="recorder">
  <button id="btn" onclick="toggleRecord()">REC</button>
  <div id="status">Click to record</div>
</div>
<div id="result"></div>
<div class="upload">
  <p style="font-size:13px;color:#737373;margin-bottom:8px;">Or upload a file:</p>
  <input type="file" accept="audio/*" onchange="uploadFile(this)">
</div>
<script>
let mediaRecorder, chunks = [], recording = false;
async function toggleRecord() {
  const btn = document.getElementById('btn');
  const status = document.getElementById('status');
  if (!recording) {
    const stream = await navigator.mediaDevices.getUserMedia({audio: true});
    mediaRecorder = new MediaRecorder(stream);
    chunks = [];
    mediaRecorder.ondataavailable = e => chunks.push(e.data);
    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach(t => t.stop());
      const blob = new Blob(chunks, {type: 'audio/webm'});
      await transcribe(blob, 'recording.webm');
    };
    mediaRecorder.start();
    recording = true;
    btn.textContent = 'STOP';
    btn.classList.add('recording');
    status.textContent = 'Recording...';
  } else {
    mediaRecorder.stop();
    recording = false;
    btn.textContent = 'REC';
    btn.classList.remove('recording');
    status.textContent = 'Transcribing...';
  }
}
async function uploadFile(input) {
  if (!input.files[0]) return;
  document.getElementById('status').textContent = 'Transcribing...';
  await transcribe(input.files[0], input.files[0].name);
}
async function transcribe(blob, filename) {
  const form = new FormData();
  form.append('audio', blob, filename);
  const start = Date.now();
  try {
    const res = await fetch('/transcribe', {method: 'POST', body: form});
    const data = await res.json();
    const el = document.getElementById('result');
    el.textContent = data.text || data.error || 'No result';
    document.getElementById('status').textContent =
      `${data.duration_s}s audio | ${data.inference_ms}ms inference | ${data.rtf}x realtime`;
  } catch(e) {
    document.getElementById('result').textContent = 'Error: ' + e.message;
    document.getElementById('status').textContent = 'Failed';
  }
}
</script></body></html>'''
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())

    def log_message(self, format, *args):
        if isinstance(format, str) and args:
            msg = format % args
        else:
            msg = str(format)
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main():
    import config as cfg
    conf = cfg.load()
    server_conf = conf["server"]

    parser = argparse.ArgumentParser(description="Parakeet STT server (MLX)")
    parser.add_argument("--port", type=int, default=server_conf.get("port", 8222))
    parser.add_argument("--host", default=server_conf.get("host", "0.0.0.0"))
    parser.add_argument("--model", default=server_conf.get("model", DEFAULT_MODEL),
                        help=f"Model to load: {', '.join(MODELS.keys())} or a HuggingFace ID (default: {DEFAULT_MODEL})")
    args = parser.parse_args()

    load_model(args.model)

    server = HTTPServer((args.host, args.port), STTHandler)
    print(f"\nSTT server running at http://0.0.0.0:{args.port}", flush=True)
    print(f"  Web UI:     http://localhost:{args.port}", flush=True)
    print(f"  API:        curl -X POST http://localhost:{args.port}/transcribe -F audio=@file.wav", flush=True)
    print(f"  Health:     http://localhost:{args.port}/health", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


if __name__ == "__main__":
    main()
