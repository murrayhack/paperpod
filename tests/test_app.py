"""Button wiring, with gpiozero stubbed out.

``Buttons.__init__`` imports gpiozero inside the function precisely so it can
be replaced here, which lets the press/hold logic be exercised without a Pi.
"""

import logging
import sys
import types

import pytest

from paperpod import bindings
from paperpod.app import Buttons


class FakeButton:
    """Stands in for gpiozero.Button, capturing the callbacks assigned to it."""

    def __init__(self, pin, pull_up=None, bounce_time=None, hold_time=None):
        self.pin = pin
        self.when_released = None
        self.when_held = None
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture
def fake_gpiozero(monkeypatch):
    module = types.ModuleType("gpiozero")
    module.Button = FakeButton
    monkeypatch.setitem(sys.modules, "gpiozero", module)
    return module


class FakePlayer:
    def __init__(self):
        self.calls = []

    def input(self, action):
        self.calls.append(f"input:{action}")

    def volume_up(self):
        self.calls.append("volume_up")

    def next_track(self):
        self.calls.append("next_track")

    def play_pause(self):
        self.calls.append("play_pause")


class FakeMode:
    def __init__(self, in_menu=False):
        self.in_menu = in_menu
        self.noted = []

    def note(self, command):
        self.noted.append(command)


def build(fake_gpiozero, buttons=None):
    player, mode = FakePlayer(), FakeMode()
    wiring = buttons or {
        13: bindings.Binding(menu="select", playing="play_pause", hold="next_track")
    }
    return Buttons(player, mode, buttons=wiring), player, mode


def test_a_press_is_logged_at_info_with_its_gpio(fake_gpiozero, caplog):
    """A press must be visible in the journal without --verbose.

    The unit does not pass it, so while dispatch logged at debug a working
    button and a dead one looked identical -- and "which button is dead" is
    the only question this log exists to answer.
    """
    buttons, player, _ = build(fake_gpiozero)

    with caplog.at_level(logging.INFO, logger="paperpod.app"):
        buttons._devices[0].when_released()

    assert player.calls == ["play_pause"]
    records = [r for r in caplog.records if r.levelname == "INFO"]
    assert len(records) == 1
    assert "13" in records[0].getMessage()
    assert "play_pause" in records[0].getMessage()


def test_a_hold_is_logged_as_held(fake_gpiozero, caplog):
    buttons, player, _ = build(fake_gpiozero)

    with caplog.at_level(logging.INFO, logger="paperpod.app"):
        buttons._devices[0].when_held()

    assert player.calls == ["next_track"]
    assert "held" in caplog.records[-1].getMessage()


def test_a_hold_swallows_the_release_that_follows_it(fake_gpiozero):
    """One press must never do two things.

    gpiozero fires when_held and then when_released on the same press, so
    without this the hold command and the press command would both run.
    """
    buttons, player, _ = build(fake_gpiozero)

    buttons._devices[0].when_held()
    buttons._devices[0].when_released()

    assert player.calls == ["next_track"]

    # The suppression is one-shot: the next press dispatches normally.
    buttons._devices[0].when_released()
    assert player.calls == ["next_track", "play_pause"]


def test_the_press_command_follows_the_mode(fake_gpiozero):
    buttons, player, mode = build(fake_gpiozero)
    mode.in_menu = True

    buttons._devices[0].when_released()

    # `select` is navigation, so it goes to the panel's input API rather than
    # to Mopidy -- the division the whole binding table exists to express.
    assert player.calls == ["input:select"]
    assert mode.noted == ["select"]


def test_close_closes_every_device(fake_gpiozero):
    buttons, _, _ = build(
        fake_gpiozero,
        buttons={
            5: bindings.Binding(menu="up", playing="volume_up"),
            13: bindings.Binding(menu="select", playing="play_pause"),
        },
    )

    buttons.close()

    assert all(device.closed for device in buttons._devices)
