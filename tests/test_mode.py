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
