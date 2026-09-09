"""Map GPIO buttons onto Mopidy and the e-paper panel.

Two APIs sit behind this, and which one a press goes to depends on what it
means rather than where it came from:

* **Navigation** — moving the cursor, opening the menu, locking the panel — is
  the panel's own business, so it goes to ``POST /epaper/input/<action>``.
* **Volume and transport** belong to Mopidy, which already owns the mixer and
  the tracklist, so they go to its JSON-RPC API.

mopidy-epaper deliberately exposes no volume actions. This is where that
division gets paid for, and it is a fair price: the panel displays a volume it
does not control, and there is exactly one route to the mixer.
"""

import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)


class Player:
    """Everything a button can do, over HTTP.

    Split from the GPIO wiring so the mapping can be exercised with no pins
    attached — see ``tests/``.
    """

    # /epaper/status waits on the panel's frontend actor, which a full refresh
    # holds for a couple of seconds; mopidy-epaper gives up on it after 3. Wait
    # longer than that, or we time out first and report a panel that answered
    # perfectly well as unreachable.
    STATUS_TIMEOUT = 4

    def __init__(
        self, base_url="http://localhost:6680", timeout=2, volume_step=5,
        status_timeout=STATUS_TIMEOUT,
    ):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._status_timeout = status_timeout
        self._volume_step = volume_step
        # Mopidy serves the panel API, and systemd releases us when its process
        # execs rather than when it is listening, so the first polls of a boot
        # are expected to fail. Stay quiet until the panel has answered once;
        # after that, a failure is real news and worth a warning.
        self._panel_seen = False

    # -- the panel -------------------------------------------------------

    def input(self, action):
        """Send a navigation action to the panel.

        Returns 202 immediately: a full refresh takes seconds on a Pi Zero, so
        the panel never makes the caller wait for one.
        """
        return self._post(f"{self._base_url}/epaper/input/{action}")

    def panel_status(self):
        """``locked``, ``asleep`` and ``in_menu``, or None if unreachable.

        Waits on the frontend actor, which a full refresh can hold for a couple
        of seconds — poll this on a timer, do not call it per press.
        """
        try:
            with urllib.request.urlopen(
                f"{self._base_url}/epaper/status", timeout=self._status_timeout
            ) as response:
                status = json.load(response)
        except (OSError, ValueError) as exc:
            log = logger.warning if self._panel_seen else logger.debug
            log("Could not read panel status: %s", exc)
            return None
        self._panel_seen = True
        return status

    # -- Mopidy ----------------------------------------------------------

    def rpc(self, method, **params):
        """Call a Mopidy JSON-RPC method. Returns its result, or None."""
        payload = {"jsonrpc": "2.0", "id": 1, "method": method}
        if params:
            payload["params"] = params

        request = urllib.request.Request(
            f"{self._base_url}/mopidy/rpc",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return json.load(response).get("result")
        except (OSError, ValueError) as exc:
            logger.warning("%s failed: %s", method, exc)
            return None

    def volume_up(self):
        return self._nudge_volume(self._volume_step)

    def volume_down(self):
        return self._nudge_volume(-self._volume_step)

    def _nudge_volume(self, delta):
        # Read before writing: the volume may have been changed from the web
        # remote or another client, and a local counter would drift from it.
        current = self.rpc("core.mixer.get_volume")
        if current is None:
            return None
        target = max(0, min(100, current + delta))
        return self.rpc("core.mixer.set_volume", volume=target)

    def toggle_mute(self):
        muted = self.rpc("core.mixer.get_mute")
        if muted is None:
            return None
        return self.rpc("core.mixer.set_mute", mute=not muted)

    def play_pause(self):
        state = self.rpc("core.playback.get_state")
        if state == "playing":
            return self.rpc("core.playback.pause")
        return self.rpc("core.playback.resume" if state == "paused" else "core.playback.play")

    def next_track(self):
        return self.rpc("core.playback.next")

    def previous_track(self):
        return self.rpc("core.playback.previous")

    def _post(self, url):
        request = urllib.request.Request(url, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return response.status
        except urllib.error.HTTPError as exc:
            logger.warning("%s: %s %s", url, exc.code, exc.reason)
            return exc.code
        except OSError as exc:
            logger.warning("%s: %s", url, exc)
            return None
