#!/usr/bin/env python3
"""
Hotkey-triggered speech-to-text with auto-paste. Superwhisper replacement.

Usage:
  python3 stt-cli.py                              # defaults from config
  python3 stt-cli.py --hotkey ctrl+shift+space     # custom hotkey
  python3 stt-cli.py --push-to-talk                # hold to record
  python3 stt-cli.py --server http://myhost:8222   # remote server
  python3 stt-cli.py --no-indicator                # disable waveform popup

Requires:
  - STT server running (locally or on the network)
  - Accessibility permissions for this terminal app
  - sox (brew install sox)
"""

import subprocess
import sys
import os
import time
import tempfile
import json
import signal
import threading
import logging
from urllib.request import urlopen, Request

# Set up file logging (frozen apps swallow stdout)
_log_dir = os.path.expanduser("~/.config/ren-stt")
os.makedirs(_log_dir, exist_ok=True)
logging.basicConfig(
    filename=os.path.join(_log_dir, "client.log"),
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ren-stt")

SAMPLE_RATE = 16000
if getattr(sys, 'frozen', False):
    SCRIPT_DIR = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# macOS system sounds
# When frozen (PyInstaller), sys.executable is the Mach-O binary.
# Subprocess scripts need a real Python interpreter.
if getattr(sys, 'frozen', False):
    import shutil
    PYTHON_EXE = shutil.which("python3") or "/usr/bin/python3"
else:
    PYTHON_EXE = sys.executable

SOUND_START = "/System/Library/Sounds/Tink.aiff"
SOUND_STOP = "/System/Library/Sounds/Pop.aiff"
SOUND_ERROR = "/System/Library/Sounds/Basso.aiff"


def eat_space():
    """Delete the non-breaking space that Option+Space inserts on macOS."""
    subprocess.Popen(
        ["osascript", "-e",
         'tell application "System Events" to key code 51'],  # 51 = backspace
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


_server_url = "http://localhost:8222"

def get_server():
    return _server_url

# ── State ───────────────────────────────────────────────────────────

recording = False
record_process = None
temp_file = None
indicator_process = None
menubar_process = None
use_indicator = True
indicator_sensitivity = 18


def play_sound(path):
    """Play a system sound asynchronously."""
    subprocess.Popen(
        ["afplay", "-v", "0.5", path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def menubar_send(state):
    """Send a state update to the menu bar process."""
    if menubar_process and menubar_process.stdin:
        try:
            menubar_process.stdin.write(f"{state}\n".encode())
            menubar_process.stdin.flush()
        except Exception:
            pass


def start_menubar():
    """Launch the menu bar icon."""
    global menubar_process
    try:
        menubar_process = subprocess.Popen(
            [PYTHON_EXE, os.path.join(SCRIPT_DIR, "stt-menubar.py")],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        menubar_process = None


def stop_menubar():
    """Close the menu bar icon."""
    global menubar_process
    if menubar_process:
        try:
            menubar_process.stdin.close()
        except Exception:
            pass
        try:
            menubar_process.terminate()
            menubar_process.wait(timeout=2)
        except Exception:
            try:
                menubar_process.kill()
            except Exception:
                pass
        menubar_process = None


def show_indicator():
    """Launch the floating waveform indicator window."""
    global indicator_process
    if not use_indicator:
        return
    try:
        indicator_process = subprocess.Popen(
            [PYTHON_EXE, os.path.join(SCRIPT_DIR, "stt-indicator.py"),
             "--sensitivity", str(indicator_sensitivity)],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        indicator_process = None


def hide_indicator():
    """Close the floating indicator window."""
    global indicator_process
    if indicator_process:
        try:
            indicator_process.stdin.close()
        except Exception:
            pass
        try:
            indicator_process.terminate()
            indicator_process.wait(timeout=2)
        except Exception:
            try:
                indicator_process.kill()
            except Exception:
                pass
        indicator_process = None


def check_accessibility():
    """Check if we have Accessibility permissions. If not, prompt and exit cleanly."""
    try:
        import ctypes, ctypes.util
        lib = ctypes.cdll.LoadLibrary(ctypes.util.find_library("ApplicationServices"))
        lib.AXIsProcessTrusted.restype = ctypes.c_bool
        trusted = lib.AXIsProcessTrusted()
        log.info("AXIsProcessTrusted: %s", trusted)

        if not trusted:
            # Try to open settings for the user
            log.info("Not trusted — opening Accessibility settings")
            subprocess.Popen(
                ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            sys.exit(2)
    except Exception as e:
        log.warning("Could not check accessibility: %s", e)


def check_deps():
    """Check that sox is installed."""
    try:
        subprocess.run(["sox", "--version"], capture_output=True, check=True)
    except FileNotFoundError:
        print("sox not found. Install with: brew install sox")
        sys.exit(1)


def check_server():
    """Check if STT server is running."""
    try:
        with urlopen(f"{get_server()}/health", timeout=2) as resp:
            data = json.loads(resp.read())
            if data.get("ready"):
                return True
    except Exception:
        pass
    return False


def start_recording():
    """Start recording audio from the default mic."""
    global recording, record_process, temp_file

    if recording:
        return

    temp_file = tempfile.mktemp(suffix=".wav")
    record_process = subprocess.Popen(
        ["sox", "-d", "-r", str(SAMPLE_RATE), "-c", "1", "-b", "16", temp_file],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    recording = True
    eat_space()
    play_sound(SOUND_START)
    show_indicator()
    menubar_send("recording")
    log.info("Recording started: %s", temp_file)
    print("\r\033[91m● Recording...\033[0m", end="", flush=True)


def stop_recording_and_transcribe():
    """Stop recording, transcribe, copy to clipboard, and paste."""
    global recording, record_process, temp_file

    if not recording:
        return

    # Stop sox
    record_process.terminate()
    record_process.wait()
    recording = False

    eat_space()
    play_sound(SOUND_STOP)
    hide_indicator()
    menubar_send("transcribing")
    log.info("Recording stopped, transcribing...")
    print("\r\033[93m◉ Transcribing...\033[0m", end="", flush=True)

    # Check file exists and has content
    file_size = os.path.getsize(temp_file) if os.path.exists(temp_file) else 0
    log.info("Audio file: %s (%d bytes)", temp_file, file_size)
    if not os.path.exists(temp_file) or file_size < 1000:
        log.info("Too short, skipped")
        print("\r\033[90m○ Too short, skipped\033[0m    ", flush=True)
        menubar_send("idle")
        cleanup()
        return

    # Send to STT server
    try:
        with open(temp_file, "rb") as f:
            audio_data = f.read()

        # Build multipart form data manually
        boundary = "----SttCliBoundary"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="audio"; filename="recording.wav"\r\n'
            f"Content-Type: audio/wav\r\n\r\n"
        ).encode() + audio_data + f"\r\n--{boundary}--\r\n".encode()

        req = Request(
            f"{get_server()}/transcribe",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())

        text = result.get("text", "").strip()
        inference_ms = result.get("inference_ms", 0)
        duration_s = result.get("duration_s", 0)
        rtf = result.get("rtf", 0)
        log.info("Transcription result: '%s' (%dms, %.1fs audio)", text, inference_ms, duration_s)

        if not text:
            print(f"\r\033[90m○ No speech detected ({duration_s}s)\033[0m    ", flush=True)
            menubar_send("idle")
            cleanup()
            return

        # Copy to clipboard
        proc = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
        proc.communicate(text.encode())

        # Paste into active window
        subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to keystroke "v" using command down'],
            capture_output=True,
        )

        print(f"\r\033[92m✓ {text[:60]}{'...' if len(text) > 60 else ''}\033[0m")
        print(f"  \033[90m{duration_s}s audio → {inference_ms}ms ({rtf}x RT)\033[0m")

    except Exception as e:
        log.error("Transcription error: %s", e, exc_info=True)
        play_sound(SOUND_ERROR)
        print(f"\r\033[91m✗ Error: {e}\033[0m")

    menubar_send("idle")
    cleanup()


def cancel_recording():
    """Cancel recording without transcribing."""
    global recording, record_process
    if not recording:
        return
    record_process.terminate()
    record_process.wait()
    recording = False
    hide_indicator()
    menubar_send("idle")
    play_sound(SOUND_STOP)
    print("\r\033[90m○ Cancelled\033[0m            ", flush=True)
    cleanup()


def cleanup():
    """Remove temp file."""
    global temp_file
    if temp_file and os.path.exists(temp_file):
        os.unlink(temp_file)
    temp_file = None


def parse_hotkey(hotkey_combo):
    """Parse a hotkey string into (modifiers_needed, trigger_key) for pynput."""
    from pynput import keyboard

    parts = hotkey_combo.lower().replace("cmd", "cmd_l").replace("ctrl", "ctrl_l").replace("shift", "shift_l").split("+")

    key_map = {
        "cmd_l": keyboard.Key.cmd, "ctrl_l": keyboard.Key.ctrl,
        "shift_l": keyboard.Key.shift, "alt": keyboard.Key.alt,
        "option": keyboard.Key.alt,
        "space": keyboard.Key.space,
    }
    for i in range(1, 13):
        key_map[f"f{i}"] = getattr(keyboard.Key, f"f{i}")

    modifiers_needed = set()
    for part in parts[:-1]:
        if part in key_map:
            modifiers_needed.add(key_map[part])

    last_part = parts[-1]
    trigger_key = key_map.get(last_part) or keyboard.KeyCode.from_char(last_part)

    return modifiers_needed, trigger_key


def run_push_to_talk(hotkey_combo):
    """Push-to-talk mode: hold hotkey to record, release to transcribe."""
    from pynput import keyboard

    modifiers_needed, trigger_key = parse_hotkey(hotkey_combo)
    pressed_modifiers = set()
    processing = False

    def on_press(key):
        nonlocal pressed_modifiers
        if key == keyboard.Key.esc and recording:
            cancel_recording()
            return

        if key in modifiers_needed:
            pressed_modifiers.add(key)

        if key == trigger_key and modifiers_needed.issubset(pressed_modifiers):
            if not recording and not processing:
                start_recording()

    def on_release(key):
        nonlocal pressed_modifiers, processing
        if key in modifiers_needed:
            pressed_modifiers.discard(key)

        if key == trigger_key and recording and not processing:
            processing = True
            def do_transcribe():
                nonlocal processing
                try:
                    stop_recording_and_transcribe()
                finally:
                    processing = False
            threading.Thread(target=do_transcribe, daemon=True).start()

    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()


def run_toggle(hotkey_combo):
    """Toggle mode: press hotkey to start, press again to stop and transcribe."""
    from pynput import keyboard

    modifiers_needed, trigger_key = parse_hotkey(hotkey_combo)
    pressed_modifiers = set()
    processing = False

    def on_press(key):
        nonlocal pressed_modifiers, processing
        if key == keyboard.Key.esc and recording and not processing:
            cancel_recording()
            return

        if key in modifiers_needed:
            pressed_modifiers.add(key)

        if key == trigger_key and modifiers_needed.issubset(pressed_modifiers):
            if not recording and not processing:
                start_recording()
            elif recording and not processing:
                processing = True
                def do_transcribe():
                    nonlocal processing
                    try:
                        stop_recording_and_transcribe()
                    finally:
                        processing = False
                threading.Thread(target=do_transcribe, daemon=True).start()

    def on_release(key):
        nonlocal pressed_modifiers
        if key in modifiers_needed:
            pressed_modifiers.discard(key)

    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()


def main():
    log.info("=== ren-stt starting (frozen=%s) ===", getattr(sys, 'frozen', False))

    # Handle PyInstaller frozen environment
    if getattr(sys, 'frozen', False):
        meipass = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
        log.info("_MEIPASS: %s", meipass)
        sys.path.insert(0, meipass)

    log.info("Importing config...")
    import config as cfg
    log.info("Loading config...")
    conf = cfg.load()
    client = conf["client"]
    log.info("Config loaded: server=%s hotkey=%s", client["server_url"], client["hotkey"])

    import argparse
    parser = argparse.ArgumentParser(description="Hotkey-triggered STT with auto-paste")
    parser.add_argument("--hotkey", default=client["hotkey"],
                        help=f"Hotkey combo (default: {client['hotkey']})")
    parser.add_argument("--toggle", action="store_true", default=(client["mode"] == "toggle"),
                        help="Toggle mode: press to start/stop (default)")
    parser.add_argument("--push-to-talk", action="store_true",
                        help="Push-to-talk mode: hold to record, release to transcribe")
    parser.add_argument("--no-indicator", action="store_true", default=(not client["indicator"]),
                        help="Disable the floating waveform indicator window")
    parser.add_argument("--sensitivity", type=float, default=client["sensitivity"],
                        help=f"Audio level sensitivity (default: {client['sensitivity']})")
    parser.add_argument("--server", default=client["server_url"],
                        help=f"STT server URL (default: {client['server_url']})")
    args = parser.parse_args()

    global _server_url, use_indicator, indicator_sensitivity
    _server_url = args.server
    use_indicator = not args.no_indicator
    indicator_sensitivity = args.sensitivity

    log.info("Checking deps...")
    check_deps()
    log.info("Checking accessibility...")
    check_accessibility()
    log.info("Checking server...")

    if not check_server():
        print(f"STT server not reachable at {get_server()}")
        print("Start the server or check --server URL.")
        sys.exit(1)

    mode = "push-to-talk" if args.push_to_talk else "toggle"
    log.info("All checks passed. Starting in %s mode.", mode)
    print(f"ren-stt ready — {mode} mode")
    print(f"Hotkey: {args.hotkey}")
    print(f"Server: {get_server()}")
    print(f"Indicator: {'on' if use_indicator else 'off'}")
    print()
    if mode == "push-to-talk":
        print("Hold the hotkey to record, release to transcribe and paste.")
    else:
        print("Press the hotkey to start recording, press again to stop and paste.")
    print("Escape to cancel a recording.")
    print("Ctrl+C to quit.\n")

    # Subprocess-based UI (menubar/indicator) needs a real Python with deps.
    # Skip in frozen mode — the main hotkey listener still works.
    if not getattr(sys, 'frozen', False):
        start_menubar()

    def shutdown(*_):
        hide_indicator()
        if not getattr(sys, 'frozen', False):
            stop_menubar()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)

    if args.push_to_talk:
        run_push_to_talk(args.hotkey)
    else:
        run_toggle(args.hotkey)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.error("Fatal: %s", e, exc_info=True)
        raise
