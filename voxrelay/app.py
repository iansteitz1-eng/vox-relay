"""Vox Relay — macOS menu-bar app (rumps). Everything macOS-only lives here; core.py is pure Python.

Menu:  Relay: ON/OFF · Threads… (40 most recent chats, ticked = relayed) · Cloud push: ON/OFF
       · Open relay.jsonl · Grant Full Disk Access… · Quit
A 5-second timer calls Relay.poll_once(). Every error is shown ONCE as a notification and
logged to ~/Library/Logs/VoxRelay.log — the app never crashes on a bad poll.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import traceback

from voxrelay import __version__
from voxrelay import core

LOG_PATH = os.path.expanduser("~/Library/Logs/VoxRelay.log")
FDA_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"
POLL_SECONDS = 5
THREADS_LIMIT = 40
APP_NAME = "Vox Relay"


def _setup_logging() -> logging.Logger:
    log = logging.getLogger("voxrelay")
    if not log.handlers:
        log.setLevel(logging.INFO)
        try:
            os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
            h = logging.FileHandler(LOG_PATH, encoding="utf-8")
        except OSError:
            h = logging.StreamHandler(sys.stderr)
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(h)
    return log


def _open(target: str) -> None:
    """`open <url-or-path>` — macOS only; failures are logged, never raised."""
    try:
        subprocess.Popen(["open", target])
    except Exception as e:  # pragma: no cover - macOS only
        logging.getLogger("voxrelay").error("open %s failed: %s", target, e)


def main() -> None:
    log = _setup_logging()
    log.info("Vox Relay %s starting", __version__)
    try:
        import rumps
    except ImportError:
        msg = "rumps is not installed — this is the macOS menu-bar app. Run: pip install rumps  (macOS only)"
        log.error(msg)
        print(msg, file=sys.stderr)
        sys.exit(2)

    class VoxRelayApp(rumps.App):
        def __init__(self):
            super().__init__(APP_NAME, title="⇄", quit_button=None)
            self.state = core.RelayState.load()
            self.relay = core.Relay(self.state, log=lambda m: log.warning(m))
            self._seen_errors: set[str] = set()
            self.item_relay = rumps.MenuItem("", callback=self.toggle_relay)
            self.threads_menu = rumps.MenuItem("Threads…")
            self.item_push = rumps.MenuItem("", callback=self.toggle_push)
            self.menu = [
                self.item_relay,
                self.threads_menu,
                self.item_push,
                None,
                rumps.MenuItem("Open relay.jsonl", callback=self.open_jsonl),
                rumps.MenuItem("Grant Full Disk Access…", callback=self.grant_fda),
                None,
                rumps.MenuItem(f"Vox Relay v{__version__}", callback=None),
                rumps.MenuItem("Quit", callback=self.quit),
            ]
            self._render_toggles()
            self.rebuild_threads()
            self.timer = rumps.Timer(self.tick, POLL_SECONDS)
            self.timer.start()

        # ---------------------------------------------------------------- helpers
        def _render_toggles(self):
            self.item_relay.title = "Relay: ON" if self.state.relay_on else "Relay: OFF"
            self.item_push.title = "Cloud push: OFF" if self.state.local_only else "Cloud push: ON"
            self.title = "⇄" if self.state.relay_on else "⇄ ·"

        def _error_once(self, key: str, text: str, exc: BaseException | None = None):
            if exc is not None:
                log.error("%s: %s\n%s", key, text, "".join(traceback.format_exception(exc)))
            else:
                log.error("%s: %s", key, text)
            if key in self._seen_errors:
                return
            self._seen_errors.add(key)
            try:
                rumps.notification(APP_NAME, key, text)
            except Exception as e:  # notifications need a bundle id; never fatal
                log.warning("notification failed: %s", e)

        def _save(self):
            try:
                self.state.save()
            except Exception as e:
                self._error_once("Could not save settings", str(e), e)

        # ---------------------------------------------------------------- threads submenu
        def rebuild_threads(self):
            try:
                self.threads_menu.clear()
            except Exception:
                pass
            self.threads_menu.add(rumps.MenuItem("Refresh list", callback=lambda _: self.rebuild_threads()))
            self.threads_menu.add(None)
            try:
                con = core.connect()
                try:
                    chats = core.list_chats(con, THREADS_LIMIT)
                finally:
                    con.close()
            except core.RelayError as e:
                self._error_once("Cannot read Messages", str(e))
                self.threads_menu.add(rumps.MenuItem("(cannot read Messages — grant Full Disk Access)", callback=None))
                return
            except Exception as e:
                self._error_once("Cannot list threads", str(e), e)
                return
            self._seen_errors.discard("Cannot read Messages")
            if not chats:
                self.threads_menu.add(rumps.MenuItem("(no chats found)", callback=None))
            for chat in chats:
                label = core.chat_label(chat)
                item = rumps.MenuItem(label, callback=self._make_thread_toggle(chat["chat_id"]))
                item.state = 1 if chat["chat_id"] in self.state.chats else 0
                self.threads_menu.add(item)

        def _make_thread_toggle(self, chat_id: int):
            def _toggle(sender):
                try:
                    if chat_id in self.state.chats:
                        self.relay.untick(chat_id)
                        sender.state = 0
                        log.info("unticked chat %s", chat_id)
                    else:
                        self.relay.tick(chat_id)
                        sender.state = 1
                        log.info("ticked chat %s (cursor %s)", chat_id, self.state.cursor(chat_id))
                except core.RelayError as e:
                    self._error_once("Cannot read Messages", str(e))
                except Exception as e:
                    self._error_once("Thread toggle failed", str(e), e)
            return _toggle

        # ---------------------------------------------------------------- menu actions
        def toggle_relay(self, _):
            self.state.relay_on = not self.state.relay_on
            self._save()
            self._render_toggles()
            log.info("relay %s", "ON" if self.state.relay_on else "OFF")

        def toggle_push(self, _):
            if self.state.local_only:
                if not self.state.push_token:
                    try:
                        win = rumps.Window(
                            message="Paste the Vox Relay token from your purchase email.\n"
                                    "Cloud push sends each relayed message to your Vox Ordo workspace "
                                    "(text and attachment file names only — never attachment files).",
                            title="Cloud push — token", default_text="", ok="Turn on", cancel="Cancel",
                            dimensions=(360, 24))
                        resp = win.run()
                    except Exception as e:
                        self._error_once("Token prompt failed", str(e), e)
                        return
                    token = (resp.text or "").strip() if resp.clicked else ""
                    if not token:
                        log.info("cloud push left OFF (no token entered)")
                        return
                    self.state.push_token = token
                self.state.local_only = False
                log.info("cloud push ON → %s", self.state.push_url)
            else:
                self.state.local_only = True
                log.info("cloud push OFF")
            self._save()
            self._render_toggles()

        def open_jsonl(self, _):
            try:
                os.makedirs(core.OUT_DIR, exist_ok=True)
                if not os.path.exists(core.OUT):
                    open(core.OUT, "a", encoding="utf-8").close()
            except Exception as e:
                self._error_once("Cannot open relay.jsonl", str(e), e)
                return
            _open(core.OUT)

        def grant_fda(self, _):
            _open(FDA_URL)

        def quit(self, _):
            log.info("quit")
            rumps.quit_application()

        # ---------------------------------------------------------------- timer
        def tick(self, _):
            if not self.state.relay_on or not self.state.chats:
                return
            try:
                new = self.relay.poll_once()
                if new:
                    log.info("relayed %d message(s)", len(new))
                self._seen_errors.discard("Cannot read Messages")
            except core.RelayError as e:
                self._error_once("Cannot read Messages", str(e))
            except Exception as e:
                self._error_once("Poll failed", str(e), e)

    try:
        VoxRelayApp().run()
    except Exception as e:  # last line of defence: log, never a silent crash dialog
        log.critical("fatal: %s\n%s", e, traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
