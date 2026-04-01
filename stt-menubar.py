#!/usr/bin/env python3
"""
Menu bar icon for the STT daemon.
Launched as a subprocess by stt-cli.py. Closes when stdin is closed.

Shows a mic icon in the macOS menu bar. Reads state updates from stdin:
  "recording"   — switch to active (red) state
  "idle"        — switch to idle state
  "transcribing" — switch to transcribing state
"""

import sys
import threading
import signal

import AppKit
import objc
from PyObjCTools import AppHelper


class STTMenuBar(AppKit.NSObject):
    status_item = None
    status_menu_item = None

    def applicationDidFinishLaunching_(self, notification):
        # Create status bar item
        self.status_item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(
            AppKit.NSSquareStatusItemLength
        )

        # Use text-based icons (SF Symbols need 10.16+, emoji is universal)
        self.set_idle()

        # Build menu
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

        # Start stdin watcher thread
        threading.Thread(target=self.watch_stdin, daemon=True).start()

    def _sf_symbol(self, name, color=None):
        """Create an SF Symbol image for the menu bar."""
        image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
        if image is None:
            return None

        if color:
            # Tinted version — non-template so the color shows
            config = AppKit.NSImageSymbolConfiguration.configurationWithPointSize_weight_(14, 5)  # 5 = medium
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
            # Template image — macOS renders it black/white automatically
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
        else:
            button.setAttributedTitle_(self._text_title("STT", AppKit.NSColor.systemRedColor()))
        if self.status_menu_item:
            self.status_menu_item.setTitle_("Ren STT — Recording...")

    def set_transcribing(self):
        button = self.status_item.button()
        image = self._sf_symbol("waveform", AppKit.NSColor.systemOrangeColor())
        if image:
            button.setImage_(image)
            button.setTitle_("")
        else:
            button.setAttributedTitle_(self._text_title("STT", AppKit.NSColor.systemOrangeColor()))
        if self.status_menu_item:
            self.status_menu_item.setTitle_("Ren STT — Transcribing...")

    def _text_title(self, text, color):
        """Fallback styled text if SF Symbols unavailable."""
        attrs = {
            AppKit.NSFontAttributeName: AppKit.NSFont.menuBarFontOfSize_(0),
            AppKit.NSForegroundColorAttributeName: color,
        }
        return AppKit.NSAttributedString.alloc().initWithString_attributes_(text, attrs)

    @objc.python_method
    def watch_stdin(self):
        """Read state commands from parent process. Exit when stdin closes."""
        try:
            for line in sys.stdin:
                cmd = line.strip()
                if cmd == "recording":
                    AppHelper.callAfter(self.set_recording)
                elif cmd == "idle":
                    AppHelper.callAfter(self.set_idle)
                elif cmd == "transcribing":
                    AppHelper.callAfter(self.set_transcribing)
        except Exception:
            pass
        # Parent closed stdin — quit
        AppHelper.callAfter(self.quit_)

    @objc.IBAction
    def quit_(self, sender=None):
        AppKit.NSApp.terminate_(self)


def main():
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)  # no dock icon

    delegate = STTMenuBar.alloc().init()
    app.setDelegate_(delegate)

    signal.signal(signal.SIGINT, lambda *_: AppHelper.callAfter(delegate.quit_))
    signal.signal(signal.SIGTERM, lambda *_: AppHelper.callAfter(delegate.quit_))

    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
