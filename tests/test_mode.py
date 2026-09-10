"""The mode cache, without a panel to poll.

These matter more than they look: getting the mode wrong means a button does
something visible and unasked-for, which is the worst failure a physical
control can have.
"""

from paperpod.mode import PanelMode


class FakePlayer:
    def __init__(self, status=None):
        self.status = status
        self.polls = 0

    def panel_status(self):
        self.polls += 1
        return self.status


def test_starts_from_the_panel():
    player = FakePlayer({"in_menu": True, "locked": False})
    mode = PanelMode(player)

    mode.refresh()

    assert mode.in_menu is True


def test_an_unreachable_panel_leaves_the_cache_alone():
    """A dropped poll must not silently flip every button's meaning."""
    player = FakePlayer({"in_menu": True, "locked": False})
    mode = PanelMode(player)
    mode.refresh()

    player.status = None
    mode.refresh()

    assert mode.in_menu is True


def test_navigation_from_now_playing_opens_the_menu():
    """Certain enough to apply without waiting for the next poll.

    Otherwise pressing `home` then `down` quickly would send the second press
    to the volume, because the cache still said now-playing.
    """
    mode = PanelMode(FakePlayer({"in_menu": False, "locked": False}))
    mode.refresh()

    mode.note("home")

    assert mode.in_menu is True


def test_leaving_the_menu_is_not_guessed():
    """`back` exits at the root and does not deeper in — unknowable here."""
    mode = PanelMode(FakePlayer({"in_menu": True, "locked": False}))
    mode.refresh()

    mode.note("back")

    assert mode.in_menu is True
    assert mode._poll_now.is_set()


def test_volume_does_not_touch_the_mode():
    mode = PanelMode(FakePlayer({"in_menu": False, "locked": False}))
    mode.refresh()

    mode.note("volume_up")

    assert mode.in_menu is False


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _mode(status=None):
    clock = FakeClock()
    player = FakePlayer(status or {"in_menu": False, "locked": False})
    mode = PanelMode(
        player, interval=2.0, idle_interval=30.0, active_for=30.0, clock=clock
    )
    return mode, player, clock


def test_the_poll_backs_off_once_nothing_is_happening():
    """Each poll costs Mopidy far more than it costs us.

    Measured on a Pi Zero 2 W: a 2s poll was ~4% of a core in the Mopidy
    process serving it, against 0.12% for a Mopidy nobody was asking anything
    of. Polling that hard while the device sits untouched is the bulk of its
    idle draw.
    """
    mode, _, clock = _mode()

    assert mode._wait_time() == 2.0  # just constructed, treated as active

    clock.advance(30)
    assert mode._wait_time() == 30.0


def test_activity_puts_the_poll_back_to_fast():
    """menu_timeout always follows a press, so that is when speed matters."""
    mode, _, clock = _mode()
    clock.advance(60)
    assert mode._wait_time() == 30.0

    mode.note("home")
    assert mode._wait_time() == 2.0


def test_a_stale_cache_is_refreshed_on_demand():
    mode, player, clock = _mode()
    before = player.polls

    clock.advance(60)
    mode.refresh_if_stale()

    assert player.polls == before + 1


def test_a_fresh_cache_is_not_refreshed_again():
    """Otherwise every press pays a round trip the poll already covered."""
    mode, player, clock = _mode()
    mode.refresh()
    before = player.polls

    clock.advance(0.5)
    mode.refresh_if_stale()

    assert player.polls == before


def test_an_unreachable_panel_is_not_retried_on_every_press():
    """A failed refresh still counts as an attempt.

    Stamping only successes would leave the cache infinitely stale while the
    panel is down, so every press would pay for a round trip that is not
    going to work -- exactly when the device is already misbehaving.
    """
    mode, player, clock = _mode()
    player.status = None  # unreachable

    clock.advance(60)
    mode.refresh_if_stale()
    after_first = player.polls

    mode.refresh_if_stale()

    assert player.polls == after_first
