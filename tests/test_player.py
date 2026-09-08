"""The HTTP client, with urllib stubbed out."""

import io
import json

import pytest

from paperpod import player as player_module
from paperpod.player import Player


class FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class Calls(list):
    """The requests made, in order, as ``(url, body)``.

    A list so tests can index it, subclassed so it can also carry the replies
    they queue — a bare list has no ``__dict__`` to hang that on.
    """

    def __init__(self):
        super().__init__()
        self.replies = []


@pytest.fixture
def calls(monkeypatch):
    """Capture requests and reply with whatever the test queued."""
    recorded = Calls()

    def fake_urlopen(request, timeout=None):
        url = request if isinstance(request, str) else request.full_url
        body = None if isinstance(request, str) else request.data
        recorded.append((url, json.loads(body) if body else None))
        reply = recorded.replies.pop(0) if recorded.replies else {}
        return FakeResponse(json.dumps(reply).encode())

    monkeypatch.setattr(player_module.urllib.request, "urlopen", fake_urlopen)
    return recorded


def test_input_posts_to_the_panel(calls):
    Player().input("down")

    url, body = calls[0]
    assert url == "http://localhost:6680/epaper/input/down"
    assert body is None


def test_rpc_posts_a_json_envelope(calls):
    Player().rpc("core.playback.next")

    url, body = calls[0]
    assert url == "http://localhost:6680/mopidy/rpc"
    assert body["method"] == "core.playback.next"
    assert body["jsonrpc"] == "2.0"


def test_volume_is_read_before_it_is_written(calls):
    """A local counter would drift from the web remote and other clients."""
    calls.replies.append({"result": 40})
    calls.replies.append({"result": None})

    Player(volume_step=5).volume_up()

    assert calls[0][1]["method"] == "core.mixer.get_volume"
    assert calls[1][1]["method"] == "core.mixer.set_volume"
    assert calls[1][1]["params"] == {"volume": 45}


def test_volume_stops_at_the_ceiling(calls):
    calls.replies.append({"result": 98})
    calls.replies.append({"result": None})

    Player(volume_step=5).volume_up()

    assert calls[1][1]["params"] == {"volume": 100}


def test_volume_stops_at_the_floor(calls):
    calls.replies.append({"result": 2})
    calls.replies.append({"result": None})

    Player(volume_step=5).volume_down()

    assert calls[1][1]["params"] == {"volume": 0}


def test_an_unreadable_volume_is_not_written(calls):
    """Mopidy returns null for volume when there is no mixer."""
    calls.replies.append({"result": None})

    Player().volume_up()

    assert len(calls) == 1


def test_play_pause_pauses_while_playing(calls):
    calls.replies.append({"result": "playing"})
    calls.replies.append({"result": None})

    Player().play_pause()

    assert calls[1][1]["method"] == "core.playback.pause"


def test_play_pause_resumes_from_paused(calls):
    calls.replies.append({"result": "paused"})
    calls.replies.append({"result": None})

    Player().play_pause()

    assert calls[1][1]["method"] == "core.playback.resume"


def test_play_pause_starts_from_stopped(calls):
    calls.replies.append({"result": "stopped"})
    calls.replies.append({"result": None})

    Player().play_pause()

    assert calls[1][1]["method"] == "core.playback.play"


def test_status_waits_longer_than_the_panel_does(monkeypatch):
    """The panel gives up on its own actor after 3s; we must not go first.

    Otherwise a poll landing during a full refresh reports a panel that
    answered perfectly well as unreachable, and the mode cache goes stale.
    """
    timeouts = []

    def fake_urlopen(request, timeout=None):
        timeouts.append(timeout)
        return FakeResponse(b"{}")

    monkeypatch.setattr(player_module.urllib.request, "urlopen", fake_urlopen)

    Player().panel_status()

    assert timeouts[0] > 3


def test_an_unreachable_mopidy_returns_none(monkeypatch):
    def boom(request, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(player_module.urllib.request, "urlopen", boom)

    assert Player().rpc("core.playback.next") is None
    assert Player().panel_status() is None
