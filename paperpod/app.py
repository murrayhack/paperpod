"""Wire GPIO buttons to the bindings and run until stopped.

The only module that touches the GPIO, so everything else can be exercised on
any machine. The edge source is injectable for the same reason: the press,
hold and suppression rules are ordinary logic and are tested without a Pi.

Uses libgpiod rather than gpiozero. gpiozero drives this through lgpio, whose
alert thread wakes ~1540 times a second whatever the buttons are doing --
measured at 4-6% of a core, permanently, to watch five buttons. Reading edge
events straight off the character device blocks in the kernel instead, which
measured 0.05%. On a battery build the wakeup count matters as much as the CPU
time: at 1.5kHz the core never idles long enough to reach a low-power state.
"""

import argparse
import logging
import signal
import threading
import time
from datetime import timedelta

from . import bindings
from .mode import PanelMode
from .player import Player

logger = logging.getLogger(__name__)

CHIP = "/dev/gpiochip0"
DEBOUNCE = timedelta(milliseconds=50)
HOLD_TIME = 0.6

# The loop wakes at least this often even with nothing happening, so close()
# is noticed without a self-pipe. One wake a second against lgpio's 1540 is
# not worth the extra machinery to remove.
MAX_WAIT = 1.0

PRESS = "press"
RELEASE = "release"


class GpiodSource:
    """Edge events from the GPIO character device.

    Debounce is set on the line, so the kernel does it. That is the other half
    of leaving gpiozero behind: its ``bounce_time`` was a Python-side timer,
    and something had to be awake to run it.
    """

    def __init__(self, lines, chip=CHIP, debounce=DEBOUNCE):
        import gpiod
        from gpiod.line import Bias, Direction, Edge

        self._gpiod = gpiod
        self._request = gpiod.request_lines(
            chip,
            consumer="paperpod",
            config={
                tuple(lines): gpiod.LineSettings(
                    direction=Direction.INPUT,
                    edge_detection=Edge.BOTH,
                    bias=Bias.PULL_UP,
                    debounce_period=debounce,
                )
            },
        )

    def wait(self, timeout):
        """Block for up to ``timeout`` seconds. Returns ``(line, PRESS|RELEASE)``."""
        if not self._request.wait_edge_events(timeout=timedelta(seconds=timeout)):
            return []
        falling = self._gpiod.EdgeEvent.Type.FALLING_EDGE
        # pull_up with the button to ground, so falling is the press.
        return [
            (event.line_offset, PRESS if event.event_type == falling else RELEASE)
            for event in self._request.read_edge_events()
        ]

    def close(self):
        self._request.release()


class Buttons:
    """Routes edge events to the bindings.

    A held button fires its ``hold`` command and suppresses the release, so
    one press never does two things.

    One thread, one blocking wait. gpiozero used a thread per button to time
    holds; here the wait is simply given a timeout matching the next hold that
    could come due, which is the same job without the threads.
    """

    def __init__(
        self,
        player,
        mode,
        buttons=None,
        hold_time=HOLD_TIME,
        source=None,
        clock=time.monotonic,
    ):
        self._player = player
        self._mode = mode
        self._hold_time = hold_time
        self._bindings = dict(buttons or bindings.BUTTONS)
        self._clock = clock
        self._source = source or GpiodSource(self._bindings)

        self._pressed_at = {}
        self._held = set()
        self._stop = threading.Event()
        self._thread = None

        for pin, binding in self._bindings.items():
            logger.info(
                "GPIO %d -> %s in menu, %s playing%s",
                pin,
                binding.menu,
                binding.playing,
                f", {binding.hold} on hold" if binding.hold else "",
            )

    def start(self):
        self._thread = threading.Thread(target=self._run, name="Buttons", daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - a dead button loop is worse
                logger.exception("button loop error; continuing")

    def tick(self):
        """One wait-and-dispatch cycle. The unit of testing for this class."""
        events = self._source.wait(self._timeout())
        for pin, edge in events:
            if edge is PRESS or edge == PRESS:
                self._pressed_at[pin] = self._clock()
            else:
                self._release(pin)
        self._fire_due_holds()

    def _timeout(self):
        """How long the next wait may block: until the soonest hold comes due."""
        now = self._clock()
        deadlines = [
            self._pressed_at[pin] + self._hold_time
            for pin in self._pressed_at
            if pin not in self._held and self._bindings[pin].hold
        ]
        if not deadlines:
            return MAX_WAIT
        return max(0.0, min(min(deadlines) - now, MAX_WAIT))

    def _fire_due_holds(self):
        now = self._clock()
        for pin, pressed_at in list(self._pressed_at.items()):
            binding = self._bindings[pin]
            if not binding.hold or pin in self._held:
                continue
            if now - pressed_at >= self._hold_time:
                self._held.add(pin)
                self.dispatch(binding.hold, pin=pin, held=True)

    def _release(self, pin):
        self._pressed_at.pop(pin, None)
        if pin in self._held:
            # The hold already fired. Swallow the release.
            self._held.discard(pin)
            return
        # The cache may have gone stale while the poll was backed off, and
        # this is the moment it matters: the wrong value here means the button
        # does something visible and unasked-for.
        self._mode.refresh_if_stale()
        self.dispatch(self._bindings[pin].press(self._mode.in_menu), pin=pin)

    def dispatch(self, command, pin=None, held=False):
        # At info, not debug: the unit does not pass --verbose, so a debug line
        # meant a working button and a dead one looked identical in the
        # journal. One line per press is not chatty -- a press produces one
        # release, and a hold suppresses its own release.
        #
        # The pin is included because "which button is dead" is the question
        # this log exists to answer, and the mapping line at startup gives the
        # GPIO but a press on its own would not.
        logger.info(
            "GPIO %s %s -> %s",
            pin if pin is not None else "?",
            "held" if held else "pressed",
            command,
        )
        bindings.run(command, self._player)
        self._mode.note(command)

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=MAX_WAIT + 1)
        self._source.close()


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
        "--idle-poll-interval",
        type=float,
        default=30.0,
        help="Seconds between polls once untouched (default: %(default)s)",
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
    mode = PanelMode(
        player,
        interval=args.poll_interval,
        idle_interval=args.idle_poll_interval,
    )
    mode.start()
    buttons = Buttons(player, mode)
    buttons.start()

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
