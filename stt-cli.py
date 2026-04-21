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
    """No-op. CGEventTap suppresses the keystroke before it reaches the app.
    Falls through harmlessly if pynput fallback is active."""
    pass


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


_menubar_delegate = None


def menubar_send(state):
    """Send a state update to the in-process menu bar."""
    if _menubar_delegate is None:
        return
    try:
        from PyObjCTools import AppHelper
        if state == "recording":
            AppHelper.callAfter(_menubar_delegate.set_recording)
        elif state == "transcribing":
            AppHelper.callAfter(_menubar_delegate.set_transcribing)
        elif state == "idle":
            AppHelper.callAfter(_menubar_delegate.set_idle)
    except Exception:
        pass


def show_indicator():
    """Launch the floating waveform indicator window."""
    global indicator_process
    if not use_indicator:
        return
    try:
        if getattr(sys, 'frozen', False):
            # In a frozen bundle, launch the co-bundled stt-indicator executable.
            # sys.executable is RenSTT; stt-indicator lives in the same MacOS/ dir.
            indicator_exe = os.path.join(os.path.dirname(sys.executable), 'stt-indicator')
            cmd = [indicator_exe, '--sensitivity', str(indicator_sensitivity)]
        else:
            cmd = [PYTHON_EXE, os.path.join(SCRIPT_DIR, 'stt-indicator.py'),
                   '--sensitivity', str(indicator_sensitivity)]
        indicator_process = subprocess.Popen(
            cmd,
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


def _get_ax_lib():
    """Load ApplicationServices and return (lib, is_trusted_func)."""
    import ctypes, ctypes.util
    lib = ctypes.cdll.LoadLibrary(ctypes.util.find_library("ApplicationServices"))
    lib.AXIsProcessTrusted.restype = ctypes.c_bool
    return lib


def is_accessible():
    """Check if we have Accessibility permissions."""
    try:
        return _get_ax_lib().AXIsProcessTrusted()
    except Exception:
        return True  # assume yes if we can't check


def check_accessibility():
    """Check Accessibility. If not granted, returns False (caller handles waiting)."""
    trusted = is_accessible()
    log.info("AXIsProcessTrusted: %s", trusted)
    if not trusted:
        log.info("Opening Accessibility settings...")
        subprocess.Popen(
            ["open", "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_Accessibility"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    return trusted


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

    # Stop sox — SIGTERM then wait, SIGKILL if stuck
    record_process.terminate()
    try:
        record_process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        record_process.kill()
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
    try:
        record_process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        record_process.kill()
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


# macOS pynput reports alt_l/alt_r/cmd_l/cmd_r etc. for physical keys,
# but Key.alt/Key.cmd for the generic versions. Normalize so matching works.
def _normalize_key(key):
    from pynput import keyboard
    _MODIFIER_NORMALIZE = {
        keyboard.Key.alt_l: keyboard.Key.alt,
        keyboard.Key.alt_r: keyboard.Key.alt,
        keyboard.Key.alt_gr: keyboard.Key.alt,
        keyboard.Key.cmd_l: keyboard.Key.cmd,
        keyboard.Key.cmd_r: keyboard.Key.cmd,
        keyboard.Key.ctrl_l: keyboard.Key.ctrl,
        keyboard.Key.ctrl_r: keyboard.Key.ctrl,
        keyboard.Key.shift_l: keyboard.Key.shift,
        keyboard.Key.shift_r: keyboard.Key.shift,
    }
    return _MODIFIER_NORMALIZE.get(key, key)


def _parse_hotkey_keycodes(hotkey_combo):
    """Parse hotkey string into (modifier_mask, keycode) for CGEventTap."""
    import Quartz
    parts = hotkey_combo.lower().split("+")

    modifier_map = {
        "option": Quartz.kCGEventFlagMaskAlternate,
        "alt": Quartz.kCGEventFlagMaskAlternate,
        "cmd": Quartz.kCGEventFlagMaskCommand,
        "ctrl": Quartz.kCGEventFlagMaskControl,
        "shift": Quartz.kCGEventFlagMaskShift,
    }
    keycode_map = {
        "space": 49, "return": 36, "tab": 48, "escape": 53,
        "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97,
        "f7": 98, "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111,
    }

    mask = 0
    for part in parts[:-1]:
        if part in modifier_map:
            mask |= modifier_map[part]

    trigger = parts[-1]
    keycode = keycode_map.get(trigger)
    if keycode is None and len(trigger) == 1:
        # Single letter — get keycode (approximate, US keyboard)
        letter_codes = "asdfhgzxcvbqwertyuiop[]\\-=`1234567890"
        # macOS keycodes for letters (US layout)
        _letter_keycodes = {
            'a': 0, 's': 1, 'd': 2, 'f': 3, 'h': 4, 'g': 5, 'z': 6, 'x': 7,
            'c': 8, 'v': 9, 'b': 11, 'q': 12, 'w': 13, 'e': 14, 'r': 15,
            'y': 16, 't': 17, 'o': 31, 'u': 32, 'i': 34, 'p': 35, 'l': 37,
            'j': 38, 'k': 40, 'n': 45, 'm': 46,
        }
        keycode = _letter_keycodes.get(trigger)

    return mask, keycode


def run_toggle(hotkey_combo):
    """Toggle mode using CGEventTap — suppresses hotkey from reaching the active app."""
    try:
        return _run_toggle_cgeventtap(hotkey_combo)
    except Exception as e:
        log.warning("CGEventTap failed (%s), falling back to pynput", e)
        return _run_toggle_pynput(hotkey_combo)


def run_push_to_talk(hotkey_combo):
    """Push-to-talk mode using CGEventTap."""
    try:
        return _run_ptt_cgeventtap(hotkey_combo)
    except Exception as e:
        log.warning("CGEventTap failed (%s), falling back to pynput", e)
        return _run_ptt_pynput(hotkey_combo)


def _run_toggle_cgeventtap(hotkey_combo):
    """Toggle mode with CGEventTap — intercepts and suppresses the hotkey."""
    import Quartz

    modifier_mask, trigger_keycode = _parse_hotkey_keycodes(hotkey_combo)
    processing = False
    ESC_KEYCODE = 53

    def callback(proxy, event_type, event, refcon):
        nonlocal processing
        if event_type not in (Quartz.kCGEventKeyDown, Quartz.kCGEventKeyUp):
            return event

        flags = Quartz.CGEventGetFlags(event)
        keycode = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)

        # Escape cancels recording
        if keycode == ESC_KEYCODE and event_type == Quartz.kCGEventKeyDown and recording:
            cancel_recording()
            return None  # suppress

        # Check if our hotkey combo matches
        hotkey_match = (keycode == trigger_keycode and
                       (flags & modifier_mask) == modifier_mask)

        if not hotkey_match:
            return event  # pass through

        if event_type == Quartz.kCGEventKeyDown:
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
            return None  # suppress — no nbsp inserted

        if event_type == Quartz.kCGEventKeyUp:
            return None  # suppress key-up too

        return event

    tap = Quartz.CGEventTapCreate(
        Quartz.kCGSessionEventTap,
        Quartz.kCGHeadInsertEventTap,
        Quartz.kCGEventTapOptionDefault,
        Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown) | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp),
        callback,
        None,
    )

    if not tap:
        raise RuntimeError("Failed to create CGEventTap")

    log.info("CGEventTap active — hotkey suppressed from reaching apps")
    source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
    loop = Quartz.CFRunLoopGetCurrent()
    Quartz.CFRunLoopAddSource(loop, source, Quartz.kCFRunLoopCommonModes)
    Quartz.CGEventTapEnable(tap, True)
    Quartz.CFRunLoopRun()


def _run_ptt_cgeventtap(hotkey_combo):
    """Push-to-talk mode with CGEventTap."""
    import Quartz

    modifier_mask, trigger_keycode = _parse_hotkey_keycodes(hotkey_combo)
    processing = False
    ESC_KEYCODE = 53

    def callback(proxy, event_type, event, refcon):
        nonlocal processing
        if event_type not in (Quartz.kCGEventKeyDown, Quartz.kCGEventKeyUp):
            return event

        flags = Quartz.CGEventGetFlags(event)
        keycode = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)

        if keycode == ESC_KEYCODE and event_type == Quartz.kCGEventKeyDown and recording:
            cancel_recording()
            return None

        hotkey_match = (keycode == trigger_keycode and
                       (flags & modifier_mask) == modifier_mask)

        if not hotkey_match:
            return event

        if event_type == Quartz.kCGEventKeyDown:
            if not recording and not processing:
                start_recording()
            return None

        if event_type == Quartz.kCGEventKeyUp:
            if recording and not processing:
                processing = True
                def do_transcribe():
                    nonlocal processing
                    try:
                        stop_recording_and_transcribe()
                    finally:
                        processing = False
                threading.Thread(target=do_transcribe, daemon=True).start()
            return None

        return event

    tap = Quartz.CGEventTapCreate(
        Quartz.kCGSessionEventTap,
        Quartz.kCGHeadInsertEventTap,
        Quartz.kCGEventTapOptionDefault,
        Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown) | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp),
        callback,
        None,
    )

    if not tap:
        raise RuntimeError("Failed to create CGEventTap")

    log.info("CGEventTap active (push-to-talk) — hotkey suppressed")
    source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
    loop = Quartz.CFRunLoopGetCurrent()
    Quartz.CFRunLoopAddSource(loop, source, Quartz.kCFRunLoopCommonModes)
    Quartz.CGEventTapEnable(tap, True)
    Quartz.CFRunLoopRun()


# ── pynput fallback (non-macOS or Quartz unavailable) ──────────────

def _run_toggle_pynput(hotkey_combo):
    from pynput import keyboard

    modifiers_needed, trigger_key = parse_hotkey(hotkey_combo)
    pressed_modifiers = set()
    processing = False

    def on_press(key):
        nonlocal pressed_modifiers, processing
        nkey = _normalize_key(key)
        if nkey == keyboard.Key.esc and recording and not processing:
            cancel_recording()
            return

        if nkey in modifiers_needed:
            pressed_modifiers.add(nkey)

        if nkey == trigger_key and modifiers_needed.issubset(pressed_modifiers):
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
        nkey = _normalize_key(key)
        if nkey in modifiers_needed:
            pressed_modifiers.discard(nkey)

    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()


def _run_ptt_pynput(hotkey_combo):
    from pynput import keyboard

    modifiers_needed, trigger_key = parse_hotkey(hotkey_combo)
    pressed_modifiers = set()
    processing = False

    def on_press(key):
        nonlocal pressed_modifiers
        nkey = _normalize_key(key)
        if nkey == keyboard.Key.esc and recording:
            cancel_recording()
            return

        if nkey in modifiers_needed:
            pressed_modifiers.add(nkey)

        if nkey == trigger_key and modifiers_needed.issubset(pressed_modifiers):
            if not recording and not processing:
                start_recording()

    def on_release(key):
        nonlocal pressed_modifiers, processing
        nkey = _normalize_key(key)
        if nkey in modifiers_needed:
            pressed_modifiers.discard(nkey)

        if nkey == trigger_key and recording and not processing:
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


def _osascript(script):
    """Run an osascript and return stdout."""
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _first_run_setup(cfg):
    """First-run dialog: pick mode, configure server, install deps if needed."""
    config_dir = os.path.expanduser("~/.config/ren-stt")
    config_path = os.path.join(config_dir, "config.json")

    # Already configured — skip
    if os.path.exists(config_path):
        conf = cfg.load()
        if conf.get("install_mode"):
            return conf

    log.info("First-run setup starting")

    # Pick mode
    choice = _osascript('''
        display dialog "Welcome to Ren STT!

How would you like to use this machine?

• Client — Hotkey + transcription (connects to a server)
• Server — Hosts the speech-to-text model (Apple Silicon only)
• Standalone — Both server + client on this machine" \
            buttons {"Client", "Server", "Standalone"} \
            default button "Standalone" \
            with title "Ren STT Setup"
        return button returned of result
    ''')

    if choice is None:
        log.info("User cancelled setup")
        sys.exit(0)

    mode = {"Client": "client", "Server": "server", "Standalone": "standalone"}.get(choice, "client")
    log.info("User selected mode: %s", mode)

    # Client mode: ask for server URL
    server_url = "http://localhost:8222"
    if mode == "client":
        url_result = _osascript('''
            display dialog "Enter the STT server address:

(e.g. macmini.local or 192.168.1.50)" \
                default answer "" \
                buttons {"Cancel", "OK"} default button "OK" \
                with title "Ren STT — Server Address"
            return text returned of result
        ''')
        if url_result is None:
            sys.exit(0)
        if url_result:
            if not url_result.startswith("http"):
                url_result = "http://" + url_result
            if not any(c.isdigit() for c in url_result.split(":")[-1]):
                url_result += ":8222"
            server_url = url_result

    # Server/Standalone: check Apple Silicon
    if mode in ("server", "standalone"):
        import platform
        if platform.machine() != "arm64":
            _osascript('''
                display dialog "Server mode requires Apple Silicon (M1/M2/M3).

This machine is not Apple Silicon. Choose Client mode instead." \
                    buttons {"OK"} default button "OK" \
                    with title "Ren STT" with icon stop
            ''')
            # Re-run setup
            return _first_run_setup(cfg)

    # Server/Standalone: install server deps into a venv
    if mode in ("server", "standalone"):
        _osascript('''
            display dialog "Installing server dependencies (MLX + Parakeet).

This only happens once and may take a minute." \
                buttons {"OK"} default button "OK" \
                with title "Ren STT Setup" giving up after 1
        ''')
        log.info("Setting up server venv...")
        venv_dir = os.path.join(config_dir, "server-venv")
        if not os.path.exists(venv_dir):
            subprocess.run([sys.executable if not getattr(sys, 'frozen', False) else "python3",
                           "-m", "venv", venv_dir], check=True)

        venv_pip = os.path.join(venv_dir, "bin", "pip")
        req_file = os.path.join(SCRIPT_DIR, "requirements-server.txt")
        log.info("Installing server deps from %s", req_file)
        result = subprocess.run([venv_pip, "install", "-q", "-r", req_file],
                               capture_output=True, text=True)
        if result.returncode != 0:
            log.error("Server dep install failed: %s", result.stderr)
            _osascript(f'''
                display dialog "Failed to install server dependencies:

{result.stderr[:200]}" \
                    buttons {{"OK"}} default button "OK" \
                    with title "Ren STT" with icon stop
            ''')
            sys.exit(1)
        log.info("Server deps installed")

    # Check sox for client/standalone
    if mode in ("client", "standalone"):
        import shutil
        if not shutil.which("sox"):
            _osascript('''
                display dialog "Ren STT requires sox for audio recording.

Install it with:
  brew install sox

Then relaunch Ren STT." \
                    buttons {"OK"} default button "OK" \
                    with title "Ren STT" with icon caution
            ''')
            sys.exit(1)

    # Write config
    os.makedirs(config_dir, exist_ok=True)
    conf_data = {
        "install_mode": mode,
        "server": {"host": "0.0.0.0", "port": 8222},
        "client": {
            "server_url": server_url,
            "hotkey": "option+space",
            "mode": "toggle",
            "sensitivity": 18,
            "indicator": True,
        },
    }
    cfg.save(conf_data)
    log.info("Config saved: mode=%s server=%s", mode, server_url)

    _osascript('display notification "Setup complete!" with title "Ren STT"')
    return conf_data


_server_proc = None


def _start_server(conf):
    """Start the STT server from the server venv."""
    global _server_proc
    config_dir = os.path.expanduser("~/.config/ren-stt")
    venv_python = os.path.join(config_dir, "server-venv", "bin", "python3")
    server_script = os.path.join(SCRIPT_DIR, "stt-server.py")
    server_log = os.path.join(config_dir, "server.log")

    if not os.path.exists(venv_python):
        log.error("Server venv not found at %s", venv_python)
        return None

    log.info("Starting server: %s %s", venv_python, server_script)
    proc = subprocess.Popen(
        [venv_python, "-u", server_script],
        stdout=open(server_log, "a"),
        stderr=subprocess.STDOUT,
        cwd=SCRIPT_DIR,
    )
    _server_proc = proc
    log.info("Server started (PID: %d)", proc.pid)

    # Write PID for cleanup
    with open(os.path.join(config_dir, "server.pid"), "w") as f:
        f.write(str(proc.pid))

    return proc


def _start_server_watchdog(conf):
    """Background thread that monitors the server and restarts it if it dies."""
    def watchdog():
        while True:
            time.sleep(10)
            if not check_server():
                log.warning("Server health check failed — checking process")
                global _server_proc
                if _server_proc and _server_proc.poll() is not None:
                    log.warning("Server process died (exit code %d) — restarting",
                               _server_proc.returncode)
                    _start_server(conf)
                    # Wait for it to come up
                    for i in range(60):
                        time.sleep(1)
                        if check_server():
                            log.info("Server restarted successfully after %ds", i + 1)
                            break
                    else:
                        log.error("Server failed to restart")
                elif _server_proc is None:
                    log.warning("No server process — starting one")
                    _start_server(conf)
                else:
                    log.warning("Server process alive but not responding — waiting")
    t = threading.Thread(target=watchdog, daemon=True)
    t.start()
    log.info("Server watchdog started")
    return t


def main():
    log.info("=== ren-stt starting (frozen=%s) ===", getattr(sys, 'frozen', False))

    # Handle PyInstaller frozen environment
    if getattr(sys, 'frozen', False):
        meipass = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
        log.info("_MEIPASS: %s", meipass)
        sys.path.insert(0, meipass)

    log.info("Importing config...")
    import config as cfg

    # First-run setup (mode picker, server dep install)
    conf = _first_run_setup(cfg)
    if conf is None:
        log.info("Loading existing config...")
        conf = cfg.load()

    install_mode = conf.get("install_mode", "client")
    client = conf["client"]
    log.info("Mode: %s, server=%s hotkey=%s", install_mode, client["server_url"], client["hotkey"])

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

    # Server-only mode: just run the server, no hotkey listener
    if install_mode == "server":
        log.info("Server-only mode")
        server_proc = _start_server(conf)
        if server_proc:
            # Run menubar to keep app alive and show status
            try:
                _run_menubar_loop(None, None, True)
            except Exception:
                server_proc.wait()
        sys.exit(0)

    # Client or Standalone: need sox + accessibility + server
    log.info("Checking deps...")
    check_deps()
    log.info("Checking accessibility...")
    _accessibility_ok = check_accessibility()

    # Standalone: start the server first
    if install_mode == "standalone":
        log.info("Starting local server...")
        _osascript('display notification "Loading speech model (first launch takes ~30s)..." with title "Ren STT"')
        _start_server(conf)
        # Wait for server to be ready
        for i in range(60):
            time.sleep(1)
            if check_server():
                log.info("Server ready after %ds", i + 1)
                break
        else:
            log.error("Server didn't start in 60s")
        # Watchdog restarts server if it dies
        _start_server_watchdog(conf)

    log.info("Checking server...")
    if not check_server():
        log.error("STT server not reachable at %s", get_server())
        _osascript(f'''
            display dialog "Cannot reach STT server at {get_server()}.

Make sure the server is running." \
                buttons {{"OK"}} default button "OK" \
                with title "Ren STT" with icon caution
        ''')
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

    hotkey_mode = "push-to-talk" if args.push_to_talk else "toggle"
    hotkey_combo = args.hotkey

    # Run menu bar on the main thread (AppKit requires it).
    # Hotkey listener starts inside the menubar loop once accessibility is confirmed.
    try:
        _run_menubar_loop(hotkey_combo, hotkey_mode, _accessibility_ok)
    except Exception as e:
        log.warning("Menu bar failed: %s — running without it", e)
        if not _accessibility_ok:
            # Can't do anything without accessibility and no menubar to poll
            log.error("No menu bar and no accessibility — exiting")
            sys.exit(2)
        # Fall back to hotkey listener only
        if hotkey_mode == "push-to-talk":
            run_push_to_talk(hotkey_combo)
        else:
            run_toggle(hotkey_combo)


def _cleanup_server():
    """Kill the server process if we started one."""
    pid_file = os.path.expanduser("~/.config/ren-stt/server.pid")
    if os.path.exists(pid_file):
        try:
            pid = int(open(pid_file).read().strip())
            os.kill(pid, signal.SIGTERM)
            log.info("Stopped server (PID: %d)", pid)
        except (ProcessLookupError, ValueError):
            pass
        os.unlink(pid_file)


def _run_menubar_loop(hotkey_combo, hotkey_mode, accessibility_ok):
    """Set up and run the AppKit menu bar on the main thread."""
    global _menubar_delegate

    import AppKit
    import objc
    from PyObjCTools import AppHelper

    class InlineMenuBar(AppKit.NSObject):
        status_item = None
        status_menu_item = None

        def applicationDidFinishLaunching_(self, notification):
            self.status_item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(
                AppKit.NSSquareStatusItemLength
            )
            self.set_idle()

            menu = AppKit.NSMenu.alloc().init()

            self.status_menu_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Ren STT — Listening", None, ""
            )
            self.status_menu_item.setEnabled_(False)
            menu.addItem_(self.status_menu_item)
            menu.addItem_(AppKit.NSMenuItem.separatorItem())

            hotkey_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "⌥ Space to record", None, ""
            )
            hotkey_item.setEnabled_(False)
            menu.addItem_(hotkey_item)

            esc_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Esc to cancel", None, ""
            )
            esc_item.setEnabled_(False)
            menu.addItem_(esc_item)

            menu.addItem_(AppKit.NSMenuItem.separatorItem())

            quit_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Quit STT", "quit:", "q"
            )
            menu.addItem_(quit_item)
            self.status_item.setMenu_(menu)

        def _sf_symbol(self, name, color=None):
            image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
            if image is None:
                return None
            if color:
                config = AppKit.NSImageSymbolConfiguration.configurationWithPointSize_weight_(14, 5)
                image = image.imageWithSymbolConfiguration_(config)
                tinted = image.copy()
                tinted.lockFocus()
                color.set()
                rect = AppKit.NSMakeRect(0, 0, tinted.size().width, tinted.size().height)
                AppKit.NSRectFillUsingOperation(rect, AppKit.NSCompositingOperationSourceAtop)
                tinted.unlockFocus()
                tinted.setTemplate_(False)
                return tinted
            else:
                image.setTemplate_(True)
                return image

        def set_idle(self):
            button = self.status_item.button()
            image = self._sf_symbol("waveform")
            if image:
                button.setImage_(image)
                button.setTitle_("")
            else:
                button.setTitle_("STT")
            if self.status_menu_item:
                self.status_menu_item.setTitle_("Ren STT — Listening")

        def set_recording(self):
            button = self.status_item.button()
            image = self._sf_symbol("waveform", AppKit.NSColor.systemRedColor())
            if image:
                button.setImage_(image)
                button.setTitle_("")
            if self.status_menu_item:
                self.status_menu_item.setTitle_("Ren STT — Recording...")

        def set_transcribing(self):
            button = self.status_item.button()
            image = self._sf_symbol("waveform", AppKit.NSColor.systemOrangeColor())
            if image:
                button.setImage_(image)
                button.setTitle_("")
            if self.status_menu_item:
                self.status_menu_item.setTitle_("Ren STT — Transcribing...")

        @objc.IBAction
        def quit_(self, sender=None):
            hide_indicator()
            _cleanup_server()
            AppKit.NSApp.terminate_(self)

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    _menubar_delegate = InlineMenuBar.alloc().init()
    app.setDelegate_(_menubar_delegate)

    def start_hotkey_listener():
        if hotkey_combo is None:
            return  # server-only mode, no hotkeys
        log.info("Starting hotkey listener (%s, %s)", hotkey_mode, hotkey_combo)
        def run_hotkeys():
            if hotkey_mode == "push-to-talk":
                run_push_to_talk(hotkey_combo)
            else:
                run_toggle(hotkey_combo)
        threading.Thread(target=run_hotkeys, daemon=True).start()

    if accessibility_ok:
        start_hotkey_listener()
    else:
        # AppKit run loop keeps the process alive. Poll in a background thread.
        def wait_for_accessibility():
            for _ in range(150):  # 5 minutes
                time.sleep(2)
                if is_accessible():
                    log.info("Accessibility granted")
                    start_hotkey_listener()
                    return
            log.error("Accessibility not granted after 5 minutes")
            AppHelper.callAfter(lambda: AppKit.NSApp.terminate_(None))
        threading.Thread(target=wait_for_accessibility, daemon=True).start()

    log.info("Menu bar started")
    AppHelper.runEventLoop()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.error("Fatal: %s", e, exc_info=True)
        raise
