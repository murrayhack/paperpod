"""Track whether the panel is showing the menu or the now-playing screen.

This exists because buttons mean different things in each, and the panel is
the only authority on which is showing — it opens the menu on its own when a
navigation action arrives, and closes it on its own after ``menu_timeout``.

``GET /epaper/status`` answers the question, but it waits on the frontend
actor, which a full refresh can hold for a second or two on a Pi Zero. Asking
per press would put that latency in front of every button. So it is polled on
a timer and cached, and a press whose effect is certain updates the guess
straight away instead of waiting for the next poll.

Some presses are not certain — ``back`` leaves the menu at the root and does
not deeper in, and nothing here can tell which. Those ask for an early poll
rather than guessing, so the window where a button could mean the wrong thing
stays as short as the panel can answer in.
"""

import logging
import threading

from . import bindings

logger = logging.getLogger(__name__)


class PanelMode:
    def __init__(self, player, interval=2.0):
        self._player = player
        self._interval = interval
        self._lock = threading.Lock()
        self._in_menu = False
        self._locked = False
        self._stop = threading.Event()
        self._poll_now = threading.Event()
        self._thread = None

    @property
    def in_menu(self):
        with self._lock:
            return self._in_menu

    @property
    def locked(self):
        with self._lock:
            return self._locked

    def start(self):
        self.refresh()
        self._thread = threading.Thread(target=self._loop, name="PanelMode", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._poll_now.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval + 1)

    def refresh(self):
        """Ask the panel. Leaves the cache alone if it cannot be reached."""
        status = self._player.panel_status()
        if status is None:
            return
        with self._lock:
            self._in_menu = bool(status.get("in_menu"))
            self._locked = bool(status.get("locked"))

    def note(self, command):
        """Update the guess after sending ``command``."""
        with self._lock:
            if not self._in_menu and command in bindings.OPENS_MENU:
                # Certain: any navigation action from now-playing opens the
                # menu, so the next press is a menu press.
                self._in_menu = True
                return
            uncertain = self._in_menu and command in bindings.MAY_LEAVE_MENU

        if uncertain:
            self._poll_now.set()

    def _loop(self):
        while not self._stop.is_set():
            self._poll_now.wait(timeout=self._interval)
            self._poll_now.clear()
            if self._stop.is_set():
                return
            try:
                self.refresh()
            except Exception:
                logger.exception("Panel status poll failed")
