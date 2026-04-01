#!/usr/bin/env python3
"""
Floating waveform indicator for STT recording.
Launched as a subprocess by stt-cli.py. Closes when stdin is closed (parent exits).

Shows a small borderless window at bottom-center of screen with a live
audio level meter and rounded corners. Adapts to macOS light/dark mode.
Uses sounddevice for real-time mic input.

Usage:
  python3 tools/stt-indicator.py                     # auto-detect appearance
  python3 tools/stt-indicator.py --sensitivity 12     # boost quiet mics
"""

import tkinter as tk
import sys
import os
import threading
import collections
import argparse
import subprocess

# ── Config ──────────────────────────────────────────────────────────

WIDTH = 240
HEIGHT = 56
BAR_COUNT = 32
BAR_GAP = 2
CORNER_RADIUS = 14

# ── Appearance ──────────────────────────────────────────────────────

def is_dark_mode():
    """Detect macOS dark mode."""
    try:
        result = subprocess.run(
            ["defaults", "read", "-g", "AppleInterfaceStyle"],
            capture_output=True, text=True,
        )
        return "dark" in result.stdout.strip().lower()
    except Exception:
        return True  # default to dark

def get_theme():
    """Return color theme based on macOS appearance."""
    dark = is_dark_mode()
    if dark:
        return {
            "bg": "#1a1a1a",
            "border": "#333333",
            "rec_dot": "#ef4444",
            "rec_text": "#ef4444",
            "bar_low": "#ef4444",
            "bar_mid": "#f97316",
            "bar_high": "#22c55e",
            "bar_idle": "#2a2a2a",
            "transparent": "#000001",
        }
    else:
        return {
            "bg": "#f5f5f5",
            "border": "#d4d4d4",
            "rec_dot": "#dc2626",
            "rec_text": "#dc2626",
            "bar_low": "#dc2626",
            "bar_mid": "#ea580c",
            "bar_high": "#16a34a",
            "bar_idle": "#e5e5e5",
            "transparent": "#000001",
        }

# ── Audio level capture ─────────────────────────────────────────────

level_history = collections.deque([0.0] * BAR_COUNT, maxlen=BAR_COUNT)
current_rms = 0.0
sensitivity = 8  # multiplier for RMS → visual level

def start_audio_monitor():
    """Capture mic input and track RMS levels."""
    import sounddevice as sd
    import numpy as np

    def callback(indata, frames, time_info, status):
        global current_rms
        rms = float(np.sqrt(np.mean(indata ** 2)))
        current_rms = min(rms * sensitivity, 1.0)

    stream = sd.InputStream(
        samplerate=16000,
        channels=1,
        blocksize=512,
        callback=callback,
    )
    stream.start()
    return stream


# ── Rounded rectangle helper ────────────────────────────────────────

def rounded_rect(canvas, x1, y1, x2, y2, radius, **kwargs):
    """Draw a rounded rectangle on a tkinter canvas."""
    points = [
        x1 + radius, y1,
        x2 - radius, y1,
        x2, y1, x2, y1 + radius,
        x2, y2 - radius,
        x2, y2, x2 - radius, y2,
        x1 + radius, y2,
        x1, y2, x1, y2 - radius,
        x1, y1 + radius,
        x1, y1, x1 + radius, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


# ── Native macOS window styling ─────────────────────────────────────

def apply_macos_styling(root, theme):
    """Use PyObjC to set rounded corners and native appearance."""
    try:
        import AppKit
        import objc
        from CoreFoundation import kCFBooleanTrue

        # Get the NSWindow — find by matching geometry
        ns_window = None
        for window in AppKit.NSApp.windows():
            ns_window = window
            break

        if ns_window:
            # Make window background transparent for rounded corners
            ns_window.setBackgroundColor_(AppKit.NSColor.clearColor())
            ns_window.setOpaque_(False)
            ns_window.setHasShadow_(True)

            # Float above everything
            ns_window.setLevel_(AppKit.NSFloatingWindowLevel + 1)

            # Style the window's content view hierarchy
            content_view = ns_window.contentView()
            content_view.setWantsLayer_(True)
            layer = content_view.layer()
            layer.setCornerRadius_(CORNER_RADIUS)
            layer.setMasksToBounds_(True)

            # Also style the tkinter frame inside the content view
            # (this is what causes the square border — tk frame has its own bg)
            for subview in content_view.subviews():
                subview.setWantsLayer_(True)
                sub_layer = subview.layer()
                sub_layer.setCornerRadius_(CORNER_RADIUS)
                sub_layer.setMasksToBounds_(True)

            # Parse theme bg color to set on the layer itself
            bg_hex = theme["bg"].lstrip("#")
            r = int(bg_hex[0:2], 16) / 255.0
            g = int(bg_hex[2:4], 16) / 255.0
            b = int(bg_hex[4:6], 16) / 255.0
            import Quartz
            layer.setBackgroundColor_(Quartz.CGColorCreateGenericRGB(r, g, b, 1.0))

            return True
    except Exception as e:
        pass
    return False


# ── GUI ─────────────────────────────────────────────────────────────

def build_window():
    theme = get_theme()

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.attributes("-alpha", 0.0)  # start invisible

    # Position: bottom-center of screen
    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    x = (screen_w - WIDTH) // 2
    y = screen_h - HEIGHT - 80
    root.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")

    root.configure(bg=theme["bg"])

    canvas = tk.Canvas(root, width=WIDTH, height=HEIGHT, bg=theme["bg"], highlightthickness=0)
    canvas.pack()

    # Force layout, apply native styling, then fade in
    root.update_idletasks()
    root.update()
    native = apply_macos_styling(root, theme)
    root.update_idletasks()
    root.attributes("-alpha", 0.93)  # reveal after styling

    if not native:
        rounded_rect(canvas, 1, 1, WIDTH - 1, HEIGHT - 1, CORNER_RADIUS,
                     outline=theme["border"], fill=theme["bg"], width=1)

    # Recording dot
    dot_x, dot_y = 14, HEIGHT // 2
    dot_r = 5
    dot_id = canvas.create_oval(
        dot_x - dot_r, dot_y - dot_r, dot_x + dot_r, dot_y + dot_r,
        fill=theme["rec_dot"], outline=""
    )

    # Label
    canvas.create_text(26, HEIGHT // 2, text="REC", anchor="w",
                       fill=theme["rec_text"], font=("SF Mono", 10, "bold"))

    # Bars area
    bar_x_start = 62
    bar_area_width = WIDTH - bar_x_start - 12
    bar_w = max(2, (bar_area_width - BAR_GAP * (BAR_COUNT - 1)) // BAR_COUNT)
    bar_ids = []
    for i in range(BAR_COUNT):
        bx = bar_x_start + i * (bar_w + BAR_GAP)
        bar_id = canvas.create_rectangle(
            bx, HEIGHT - 10, bx + bar_w, HEIGHT - 10,
            fill=theme["bar_idle"], outline=""
        )
        bar_ids.append(bar_id)

    # Animation state
    dot_visible = [True]
    blink_counter = [0]

    def update():
        global current_rms

        level_history.append(current_rms)

        max_bar_h = HEIGHT - 18
        for i, bar_id in enumerate(bar_ids):
            level = level_history[i]
            h = max(2, int(level * max_bar_h))
            bx = bar_x_start + i * (bar_w + BAR_GAP)
            y_top = HEIGHT - 10 - h

            if level > 0.6:
                color = theme["bar_high"]
            elif level > 0.25:
                color = theme["bar_mid"]
            elif level > 0.05:
                color = theme["bar_low"]
            else:
                color = theme["bar_idle"]

            canvas.coords(bar_id, bx, y_top, bx + bar_w, HEIGHT - 10)
            canvas.itemconfig(bar_id, fill=color)

        # Blink dot
        blink_counter[0] += 1
        if blink_counter[0] % 15 == 0:
            dot_visible[0] = not dot_visible[0]
            canvas.itemconfig(dot_id, fill=theme["rec_dot"] if dot_visible[0] else theme["bg"])

        root.after(33, update)  # ~30fps

    def watch_stdin():
        """Close window when parent process closes stdin."""
        try:
            if not sys.stdin.isatty():
                while True:
                    data = sys.stdin.buffer.read(1)
                    if not data:
                        break
            else:
                return
        except Exception:
            pass
        root.after(0, root.destroy)

    threading.Thread(target=watch_stdin, daemon=True).start()
    update()
    return root


def main():
    global sensitivity

    parser = argparse.ArgumentParser(description="STT recording indicator")
    parser.add_argument("--sensitivity", type=float, default=18,
                        help="Audio level sensitivity multiplier (default: 18, lower for loud mics)")
    args = parser.parse_args()
    sensitivity = args.sensitivity

    stream = start_audio_monitor()
    try:
        root = build_window()
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        stream.stop()
        stream.close()


if __name__ == "__main__":
    main()
