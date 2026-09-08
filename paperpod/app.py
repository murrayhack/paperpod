"""Wire GPIO buttons to the bindings and run until stopped.

The only module that imports gpiozero, so everything else can be exercised on
any machine.
"""

import argparse
import logging
import signal
import threading

from . import bindings
from .mode import PanelMode
from .player import Player

logger = logging.getLogger(__name__)

BOUNCE_TIME = 0.05
HOLD_TIME = 0.6


class Buttons:
    """Holds the gpiozero objects and routes presses.

    A held button fires its ``hold`` command and suppresses the release, so
    one press never does two things.
    """

    def __init__(self, player, mode, buttons=None, hold_time=HOLD_TIME):
        import gpiozero

        self._player = player
        self._mode = mode
        self._hold_time = hold_time
        self._held = set()
        self._devices = []

        for pin, binding in (buttons or bindings.BUTTONS).items():
            device = gpiozero.Button(
                pin, pull_up=True, bounce_time=BOUNCE_TIME, hold_time=hold_time
            )
            device.when_released = self._on_release(pin, binding)
            if binding.hold:
                device.when_held = self._on_hold(pin, binding)
            self._devices.append(device)
            logger.info(
                "GPIO %d -> %s in menu, %s playing%s",
                pin,
                binding.menu,
                binding.playing,
                f", {binding.hold} on hold" if binding.hold else "",
            )

    def _on_release(self, pin, binding):
        def handler():
            if pin in self._held:
                # The hold already fired. Swallow the release.
                self._held.discard(pin)
                return
            self.dispatch(binding.press(self._mode.in_menu))

        return handler

    def _on_hold(self, pin, binding):
        def handler():
            self._held.add(pin)
            self.dispatch(binding.hold)

        return handler

    def dispatch(self, command):
        logger.debug("dispatch %s", command)
        bindings.run(command, self._player)
        self._mode.note(command)

    def close(self):
        for device in self._devices:
            device.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default="http://localhost:6680",
        help="Where Mopidy is listening (default: %(default)s)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Seconds between panel status polls (default: %(default)s)",
    )
    parser.add_argument(
        "--volume-step",
        type=int,
        default=5,
        help="Volume change per press (default: %(default)s)",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    player = Player(base_url=args.url, volume_step=args.volume_step)
    mode = PanelMode(player, interval=args.poll_interval)
    mode.start()
    buttons = Buttons(player, mode)

    # systemd stops us with SIGTERM, whose default action would skip the
    # cleanup below. Turn it into an ordinary wake-up instead.
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())

    logger.info("paperpod running against %s. Ctrl-C to stop.", args.url)
    try:
        while not stopping.wait(timeout=3600):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        buttons.close()
        mode.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
