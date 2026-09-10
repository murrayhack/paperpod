"""Button routing, driven through a fake edge source.

No GPIO and no libgpiod: ``Buttons`` takes its events from an injected source
and its time from an injected clock, so presses, holds and the suppression
rule are ordinary logic under test. That seam replaced stubbing
``sys.modules['gpiozero']``, and it is a better one -- these tests now drive
the same code path the Pi does, rather than a mock of a library.
"""

import logging

import pytest

from paperpod import bindings
from paperpod.app import PRESS, RELEASE, Buttons


class FakeSource:
    """Hands over queued events, and reports what timeout it was given.

    Each ``wait`` returns one batch. An empty queue returns nothing, which is
    what a real wait does when it times out with no edges.
    """

    def __init__(self, batches=None):
        self.batches = list(batches or [])
        self.timeouts = []
        self.closed = False

    def wait(self, timeout):
        self.timeouts.append(timeout)
        return self.batches.pop(0) if self.batches else []

    def close(self):
        self.closed = True


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakePlayer:
    def __init__(self):
        self.calls = []

    def input(self, action):
        self.calls.append(f"input:{action}")

    def volume_up(self):
        self.calls.append("volume_up")

    def volume_down(self):
        self.calls.append("volume_down")

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


WIRING = {
    13: bindings.Binding(menu="select", playing="play_pause", hold="next_track"),
    5: bindings.Binding(menu="up", playing="volume_up"),
}


@pytest.fixture
def rig():
    source, clock = FakeSource(), FakeClock()
    player, mode = FakePlayer(), FakeMode()
    buttons = Buttons(
        player, mode, buttons=WIRING, hold_time=0.6, source=source, clock=clock
    )
    return buttons, source, clock, player, mode


def test_a_tap_dispatches_on_release(rig):
    buttons, source, clock, player, _ = rig
    source.batches = [[(13, PRESS)], [(13, RELEASE)]]

    buttons.tick()
    assert player.calls == []  # nothing happens until the release

    clock.advance(0.1)
    buttons.tick()
    assert player.calls == ["play_pause"]


def test_a_hold_fires_without_a_release(rig):
    """The hold must fire while the button is still down, as gpiozero's did.

    Waiting for the release would make a hold indistinguishable from a slow
    tap until the finger came off.
    """
    buttons, source, clock, player, _ = rig
    source.batches = [[(13, PRESS)]]

    buttons.tick()
    assert player.calls == []

    clock.advance(0.6)
    buttons.tick()  # no new events, but the hold is now due
    assert player.calls == ["next_track"]


def test_a_hold_swallows_the_release_that_follows_it(rig):
    buttons, source, clock, player, _ = rig
    source.batches = [[(13, PRESS)], [], [(13, RELEASE)]]

    buttons.tick()
    clock.advance(0.6)
    buttons.tick()
    assert player.calls == ["next_track"]

    buttons.tick()  # the release
    assert player.calls == ["next_track"]

    # One-shot: the next tap dispatches normally.
    source.batches = [[(13, PRESS)], [(13, RELEASE)]]
    buttons.tick()
    clock.advance(0.1)
    buttons.tick()
    assert player.calls == ["next_track", "play_pause"]


def test_a_button_with_no_hold_binding_never_holds(rig):
    buttons, source, clock, player, _ = rig
    source.batches = [[(5, PRESS)]]

    buttons.tick()
    clock.advance(10)
    buttons.tick()

    assert player.calls == []  # still nothing: 5 has no hold command


def test_the_press_command_follows_the_mode(rig):
    buttons, source, clock, player, mode = rig
    mode.in_menu = True
    source.batches = [[(13, PRESS)], [(13, RELEASE)]]

    buttons.tick()
    clock.advance(0.1)
    buttons.tick()

    # `select` is navigation, so it goes to the panel's input API rather than
    # to Mopidy -- the division the whole binding table exists to express.
    assert player.calls == ["input:select"]
    assert mode.noted == ["select"]


def test_the_wait_is_shortened_to_the_next_hold(rig):
    """The timeout is how holds are timed, so it has to track the deadline.

    Too long and the hold fires late; unbounded and it never fires at all
    until the next edge arrives.
    """
    buttons, source, clock, _, _ = rig

    buttons.tick()
    assert source.timeouts[-1] == pytest.approx(1.0)  # idle: the ceiling

    source.batches = [[(13, PRESS)]]
    buttons.tick()
    clock.advance(0.4)
    buttons.tick()
    assert source.timeouts[-1] == pytest.approx(0.2)  # 0.6 - 0.4 remaining


def test_the_wait_never_goes_negative(rig):
    """A late wake must not be handed a negative timeout."""
    buttons, source, clock, _, _ = rig
    source.batches = [[(13, PRESS)]]

    buttons.tick()
    clock.advance(5)  # long overdue
    buttons.tick()

    assert all(timeout >= 0 for timeout in source.timeouts)


def test_holds_are_tracked_per_button(rig):
    buttons, source, clock, player, _ = rig
    source.batches = [[(13, PRESS), (5, PRESS)]]

    buttons.tick()
    clock.advance(0.6)
    buttons.tick()

    # 13 holds, 5 has no hold binding and is still waiting for its release.
    assert player.calls == ["next_track"]

    source.batches = [[(5, RELEASE)]]
    buttons.tick()
    assert player.calls == ["next_track", "volume_up"]


def test_a_press_is_logged_at_info_with_its_gpio(rig, caplog):
    """A press must be visible in the journal without --verbose.

    The unit does not pass it, so while dispatch logged at debug a working
    button and a dead one looked identical -- and "which button is dead" is
    the only question this log exists to answer.
    """
    buttons, source, clock, _, _ = rig
    source.batches = [[(13, PRESS)], [(13, RELEASE)]]

    buttons.tick()
    clock.advance(0.1)
    with caplog.at_level(logging.INFO, logger="paperpod.app"):
        buttons.tick()

    messages = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert len(messages) == 1
    assert "13" in messages[0] and "play_pause" in messages[0]


def test_a_hold_is_logged_as_held(rig, caplog):
    buttons, source, clock, _, _ = rig
    source.batches = [[(13, PRESS)]]

    buttons.tick()
    clock.advance(0.6)
    with caplog.at_level(logging.INFO, logger="paperpod.app"):
        buttons.tick()

    assert "held" in caplog.records[-1].getMessage()


def test_close_releases_the_source(rig):
    buttons, source, _, _, _ = rig

    buttons.close()

    assert source.closed


def test_the_loop_survives_a_failure(rig, caplog):
    """One bad cycle must not kill every button for the rest of the session.

    bindings.run raises on an unknown command, and a wait can fail too. Under
    gpiozero that happened inside a callback and was swallowed; here it is a
    loop that has to keep running, so _run swallows and logs rather than
    letting the thread die silently.
    """
    buttons, source, _, _, _ = rig
    waits = []

    def flaky(timeout):
        waits.append(timeout)
        if len(waits) == 1:
            raise RuntimeError("boom")
        buttons._stop.set()
        return []

    source.wait = flaky

    with caplog.at_level(logging.ERROR, logger="paperpod.app"):
        buttons._run()

    assert len(waits) >= 2, "the loop stopped at the first failure"
    assert any(r.levelname == "ERROR" for r in caplog.records)
