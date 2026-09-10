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

The polling backs off when nothing is happening, because it is not free: each
request costs Mopidy far more than it costs us. Measured on a Pi Zero 2 W, a
2s poll was 0.15% of a core here and ~4% in the Mopidy process serving it,
against 0.12% for a Mopidy nobody was asking anything of.

The panel only changes mode on its own through ``menu_timeout``, which always
follows a press, so a fast poll is only needed for a while after activity.
What that misses is a mode change with no press behind it at all -- the web
remote can open the menu -- so the first press after a quiet spell refreshes
synchronously before it decides what it means. That costs one round trip on
that press, and buys back never acting on a cache that went stale while
nobody was looking.
"""

import logging
import threading
import time

from . import bindings

logger = logging.getLogger(__name__)


class PanelMode:
    def __init__(
        self,
        player,
        interval=2.0,
        idle_interval=30.0,
        active_for=30.0,
        clock=time.monotonic,
    ):
        self._player = player
        self._interval = interval
        self._idle_interval = idle_interval
        self._active_for = active_for
        self._clock = clock
        self._lock = threading.Lock()
        self._in_menu = False
        self._locked = False
        self._stop = threading.Event()
        self._poll_now = threading.Event()
        self._thread = None
        self._last_activity = clock()
        self._last_refresh = clock()

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
        # Stamped on the attempt, not the success: an unreachable panel would
        # otherwise look infinitely stale and make every press pay for a
        # round trip that is not going to work.
        self._last_refresh = self._clock()
        status = self._player.panel_status()
        if status is None:
            return
        with self._lock:
            self._in_menu = bool(status.get("in_menu"))
            self._locked = bool(status.get("locked"))

    def refresh_if_stale(self, max_age=None):
        """Refresh now if the cache is older than a fast poll would allow.

        Called before a press is interpreted. While idle the cache can be up
        to ``idle_interval`` old, and the panel may have moved without a press
        to tell us -- so one press pays a round trip rather than risking the
        wrong command.
        """
        max_age = self._interval if max_age is None else max_age
        if self._clock() - self._last_refresh >= max_age:
            self.refresh()

    def note(self, command):
        """Update the guess after sending ``command``."""
        self._last_activity = self._clock()
        with self._lock:
            if not self._in_menu and command in bindings.OPENS_MENU:
                # Certain: any navigation action from now-playing opens the
                # menu, so the next press is a menu press.
                self._in_menu = True
                return
            uncertain = self._in_menu and command in bindings.MAY_LEAVE_MENU

        if uncertain:
            self._poll_now.set()

    def _wait_time(self):
        """Fast while the user is around, slow when they are not."""
        if self._clock() - self._last_activity < self._active_for:
            return self._interval
        return self._idle_interval

    def _loop(self):
        while not self._stop.is_set():
            self._poll_now.wait(timeout=self._wait_time())
            self._poll_now.clear()
            if self._stop.is_set():
                return
            try:
                self.refresh()
            except Exception:
                logger.exception("Panel status poll failed")
