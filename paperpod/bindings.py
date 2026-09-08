"""What each button does, and how that depends on what the panel is showing.

Kept apart from the GPIO wiring and from the HTTP calls so the mapping is
ordinary data that can be read, changed and tested without a Pi.

The mode-dependence is the whole reason this module exists. Five buttons are
not enough for both a menu and a now-playing screen, so ``up`` has to mean
*move the cursor* in one and *volume* in the other.
"""

import dataclasses


@dataclasses.dataclass(frozen=True)
class Binding:
    """One button.

    ``menu`` and ``playing`` are command names — see :data:`COMMANDS`. ``hold``
    fires instead of ``menu``/``playing`` when the button is held, and is the
    same in both modes to keep it predictable.
    """

    menu: str
    playing: str
    hold: str = None

    def press(self, in_menu):
        return self.menu if in_menu else self.playing


# Pins avoid everything already spoken for: I2S on 18, 19, 20 and 21, SPI on
# 7-11, the panel's RST 17, BUSY 24 and DC 25, and I2C on 2 and 3. A collision
# with any of those fails confusingly rather than loudly.
BUTTONS = {
    5: Binding(menu="up", playing="volume_up"),
    6: Binding(menu="down", playing="volume_down"),
    13: Binding(menu="select", playing="play_pause", hold="next_track"),
    16: Binding(menu="back", playing="previous_track"),
    # The menu button. `home` in both modes, so there is always one key that
    # gets you to the menu and one that gets you out, whatever is showing.
    26: Binding(menu="home", playing="home", hold="toggle_lock"),
}

#: Command name to what it does. Navigation goes to the panel's input API;
#: everything else is Mopidy's, and goes to JSON-RPC.
COMMANDS = {
    "up": lambda player: player.input("up"),
    "down": lambda player: player.input("down"),
    "select": lambda player: player.input("select"),
    "back": lambda player: player.input("back"),
    "home": lambda player: player.input("home"),
    "toggle_lock": lambda player: player.input("toggle_lock"),
    "toggle_shuffle": lambda player: player.input("toggle_shuffle"),
    "toggle_repeat": lambda player: player.input("toggle_repeat"),
    "wake": lambda player: player.input("wake"),
    "volume_up": lambda player: player.volume_up(),
    "volume_down": lambda player: player.volume_down(),
    "toggle_mute": lambda player: player.toggle_mute(),
    "play_pause": lambda player: player.play_pause(),
    "next_track": lambda player: player.next_track(),
    "previous_track": lambda player: player.previous_track(),
}

#: Commands that open or close the menu, so the mode cache can guess ahead of
#: the next poll instead of sending the following press to the wrong place.
OPENS_MENU = frozenset({"up", "down", "select", "home"})
MAY_LEAVE_MENU = frozenset({"home", "back"})


def run(command, player):
    """Apply a command name. Unknown names are a mapping bug, not user input."""
    try:
        handler = COMMANDS[command]
    except KeyError:
        raise KeyError(f"unknown command: {command!r}") from None
    return handler(player)
