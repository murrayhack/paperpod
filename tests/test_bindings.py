"""The mapping is data, so it can be checked without a Pi or a Mopidy."""

import pytest

from paperpod import bindings


class FakePlayer:
    def __init__(self):
        self.calls = []

    def input(self, action):
        self.calls.append(("input", action))

    def volume_up(self):
        self.calls.append(("volume_up",))

    def volume_down(self):
        self.calls.append(("volume_down",))

    def toggle_mute(self):
        self.calls.append(("toggle_mute",))

    def play_pause(self):
        self.calls.append(("play_pause",))

    def next_track(self):
        self.calls.append(("next_track",))

    def previous_track(self):
        self.calls.append(("previous_track",))


def test_every_binding_names_a_real_command():
    for pin, binding in bindings.BUTTONS.items():
        for command in (binding.menu, binding.playing, binding.hold):
            if command is not None:
                assert command in bindings.COMMANDS, f"GPIO {pin}: {command}"


def test_bindings_avoid_pins_that_are_taken():
    """I2S, SPI, the panel and I2C all have prior claims.

    A collision here is the failure mode that costs an evening: gpiozero takes
    the pin, the other device goes quiet, and nothing raises.
    """
    i2s = {18, 19, 20, 21}
    spi = {7, 8, 9, 10, 11}
    panel = {17, 24, 25}
    i2c = {2, 3}
    taken = i2s | spi | panel | i2c

    assert not set(bindings.BUTTONS) & taken


def test_the_menu_is_always_reachable():
    """At least one button must open the menu from the now-playing screen."""
    assert any(b.playing in bindings.OPENS_MENU for b in bindings.BUTTONS.values())


def test_press_depends_on_mode():
    binding = bindings.Binding(menu="up", playing="volume_up")

    assert binding.press(in_menu=True) == "up"
    assert binding.press(in_menu=False) == "volume_up"


def test_navigation_goes_to_the_panel():
    player = FakePlayer()

    bindings.run("up", player)

    assert player.calls == [("input", "up")]


def test_volume_goes_to_mopidy():
    player = FakePlayer()

    bindings.run("volume_up", player)

    assert player.calls == [("volume_up",)]


def test_unknown_command_is_a_mapping_bug():
    with pytest.raises(KeyError):
        bindings.run("teleport", FakePlayer())
