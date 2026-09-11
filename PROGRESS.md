# paperpod — progress log

Goal: the physical half of an e-paper Mopidy player. Buttons, volume and
transport for a Pi Zero 2 W with a Waveshare 2.13" V4 panel and a
PCM5100A I2S DAC, so the thing behaves like an MP3 player rather than a
computer that plays music.

## Why this is a separate repository (2026-09-08)

mopidy-epaper's input API carried shuffle and repeat but not volume,
which looked inconsistent, and the obvious fix was to add `volume_up` /
`volume_down` there. That was rejected: Mopidy already owns the mixer and
the tracklist behind JSON-RPC, so a second route to them means two ways
to do one thing, and a surface with no natural edge — seek next, then
playlists-by-name, then everything.

The panel and its navigation are what the extension owns. Everything
physical lives here instead, driving both APIs from the outside. That
division was already half-drawn: mopidy-epaper deliberately does not
talk to GPIO, and says so in its README.

It costs nothing at the seam. The panel redraws on Mopidy's
`volume_changed`, so it displays a volume it does not control, which is
the right way round. Rip the buttons out and the screen still works,
driven by the web remote.

## Decisions made

| Decision | Choice | Why |
|---|---|---|
| Not a Mopidy extension | An ordinary process speaking HTTP | Nothing here needs to live inside Mopidy, and staying outside keeps the boundary honest |
| Runtime dependencies | Standard library only, plus libgpiod | Both APIs are plain HTTP, so urllib is enough; nothing to keep current |
| Where navigation goes | `POST /epaper/input/<action>` | The panel owns its menu state; a second copy here would drift |
| Where volume and transport go | Mopidy's JSON-RPC | Mopidy owns the mixer and the tracklist |
| Button mapping | Plain data in `bindings.py` | Readable, changeable and testable without a Pi |
| GPIO access | `app.py` only, behind an injectable source | Everything else runs on any machine, and the press/hold rules are tested without a Pi |
| Knowing which screen is showing | Poll `/epaper/status` on a timer and cache it | It waits on the frontend actor, which a full refresh holds for a second or two — asking per press would put that in front of every button |
| Guessing the mode ahead of the poll | Only where certain | Navigation from now-playing always opens the menu; `back` leaves it only at the root, and guessing wrong makes a button do something visible and unasked-for |
| Volume steps | Read the current volume, then write | A local counter would drift from the web remote and other clients |

## Verified on hardware (2026-09-09)

Pi Zero 2 W, Raspberry Pi OS Trixie (Python 3.13), Mopidy 3.4.2, real
panel and a PCM5100A on `hifiberry-dac`.

- [x] 29 tests pass.
- [x] Both APIs reachable and returning the shapes expected —
      `/epaper/status` gives `locked`, `asleep`, `in_menu`, `running`;
      JSON-RPC gives a volume and a playback state. The tests stub
      urllib, so this was the part they could not confirm.
- [x] All five button pins claimed with no conflict against the panel,
      the DAC, SPI or I2C.
- [x] GPIO 13 held fires `next_track` and **suppresses the release**, so
      one press does not also fire `play_pause`. That was the least
      tested code in the repo.
- [x] GPIO 13 tapped toggles play and pause.
- [x] Both services start on boot and survive a reboot.

### Every button, both modes (2026-09-10)

Pressed on jumpers, read off `journalctl -u paperpod -f` now that
dispatch logs the GPIO and the resolved command.

| GPIO | in menu | playing | on hold |
| --- | --- | --- | --- |
| 5 | `up` | `volume_up` | — |
| 6 | `down` | `volume_down` | — |
| 13 | `select` | `play_pause` | `next_track` |
| 16 | `back` | `previous_track` | — |
| 26 | `home` | `home` | `toggle_lock` |

Every cell fired. Two things the tests could not have shown:

- **The mode switch works in the wild.** GPIO 5 logged `up` at 23:44:02
  and `volume_up` at 23:45:34 — the same button resolving differently,
  including `menu_timeout` dropping back to now-playing on its own.
- **A hold really does swallow its release on hardware.** Every
  `GPIO 13 held -> next_track` stands alone in the journal, with no
  `pressed` line behind it.

## Idle CPU: gpiozero to libgpiod (2026-09-10)

paperpod idled at 4-6% of a core doing nothing. On a battery build that is
worth chasing, and the wakeup count matters more than the percentage: nothing
that wakes 1540 times a second lets the core reach a low-power state.

The chain, because two plausible answers were wrong and are worth not
repeating:

| Step | Result |
| --- | --- |
| `--poll-interval 30` (15x fewer polls) | 6.35%, *higher* than at 2s. Not the poll loop. |
| Per-thread CPU from `/proc/PID/task/*/stat` | One thread held 4.11% of 4.7%. |
| `py-spy dump` | That thread has **no Python frames** — it is native, inside lgpio. |
| `strace -c` on it | 15,379 `ppoll` in 10s ≈ 1540/s, ~30us each ≈ 4.6%. Matches. |
| `bounce_time=None` | 6.05% vs 6.10%. Not the debounce timer either. |
| libgpiod, one blocking `wait_edge_events` | **0.05%** |
| The rewritten daemon in service | **0.20%**, against 4.64% before |
| Mopidy, BUSY as InputDevice | 4.07%, against 6.30% before |
| Mopidy with nothing polling it | **0.12%** |

Two traps in the measuring. `CPUUsageNSec` counts CPU *time*, not cycles, so
under `ondemand` the same work reads ~40% apart depending on clock — which is
why the 30s-poll run looked worse. And a cumulative counter divided by uptime
hides everything: only sampling the delta over a fixed window says anything.

So the fix was never in paperpod's code. lgpio's alert thread polls
unconditionally; gpiozero has no setting that changes it. Reading edge events
off `/dev/gpiochip0` blocks in the kernel instead, and the kernel does the
debounce.

What that bought, beyond 120x less idle CPU:

- One thread instead of six. gpiozero used a thread per button to time holds;
  the wait is now simply given a timeout matching the next hold deadline.
- `bounce_time` stops being a Python concern — `debounce_period` is a line
  setting the kernel honours.
- No gpiozero dependency, so no `GPIOZERO_PIN_FACTORY` and no writable
  working directory in the unit. Both of those were lgpio's requirements.
- A better test seam. The edge source and the clock are injected, so presses,
  holds and the suppression rule are tested as ordinary logic rather than by
  stubbing `sys.modules['gpiozero']`.

mopidy-epaper still uses gpiozero for the panel's RST and DC pins, so
`python3-gpiozero`, `python3-lgpio` and the mopidy override's lgpio settings
all stay. Only paperpod left.

Confirmed on hardware 2026-09-10: taps fire on release with the right
command on all five buttons, the mode switch still resolves `select` rather
than `play_pause` in the menu, and a hold on 13 fires `next_track` with no
press behind it. That was the part with no test under it — everything else
runs against the fake source, but `GpiodSource`'s edge decoding could only be
checked by pressing something. Inverted polarity would have shown up as taps
firing on press-down and holds never coming due.

The 0.20% in service against 0.05% standalone is `PanelMode` polling Mopidy
every 2s, now measurable for the first time with lgpio's noise gone. Worth
noting what that says in hindsight: the poll loop, the first thing suspected
and the one thing changed twice while chasing this, was about a thirtieth of
what lgpio was burning.

### The sting in the tail

With both alert threads gone, Mopidy still idled at 4.07%. Per-thread again,
then `strace`: `accept4`, `recvfrom`, `sendto`, five connections per ten
seconds -- the HTTP server, serving paperpod's 2s status poll. Stopping
paperpod dropped Mopidy to **0.12%**.

So the poll loop was a real cost after all, and every measurement that said
otherwise was taken in the wrong process. It costs ~0.15% to make the request
and ~4% to serve it: Tornado wakes, the epaper frontend is asked, a Pykka
actor answers, JSON is serialised, twice a second, forever.

The fix is in `mode.py`: poll at 2s for 30s after any activity, back off to
30s when untouched, and refresh synchronously before interpreting the first
press after a quiet spell. The panel only changes mode by itself through
`menu_timeout`, which always follows a press, so the fast window covers it;
the synchronous refresh covers the case with no press behind it at all, such
as the web remote. One press pays a round trip; none of them act on a stale
cache.

Confirmed on hardware 2026-09-10. Mopidy idles at **0.64%** with the backoff
in, against 4.07% before and 0.12% for a Mopidy nobody polls. The 0.64%
includes the first 30s after the restart, which is still inside the fast
window; steady state is nearer 0.4%.

The backoff being *safe* was checked separately from it being *cheap*, since
a stale cache makes a button do something visible and unasked-for. With the
poll backed off, the menu was opened through the HTTP API so that no button
was involved and paperpod could not know:

    curl -s -X POST http://localhost:6680/epaper/input/home
    GPIO 6 pressed -> down

`down`, not `volume_down` — the press refreshed before deciding what it
meant. That is the one sequence the old code would have got wrong, and the
only reason the backoff is allowed to exist.

Where the two processes ended up, from ~11% of a core between them:

| | before | after |
| --- | --- | --- |
| paperpod | 4.64% | ~0.20% |
| Mopidy | 6.30% | 0.64% |

Three causes, none of them where the search started: two lgpio alert threads
watching pins nothing subscribed to, and a status poll whose cost was in the
other process.

Not measured: actual power draw. 4-6% of one core is perhaps 10-20mW against a
few hundred, so the battery gain may be small; the wakeup argument suggests
more. A meter inline would settle it.

## Bring-up: five things that fail silently

Every problem in getting this running was quiet rather than loud, which
is worth recording as a pattern.

- **The panel needs 5V as well as 3.3V.** 3.3V runs the controller; the
  ±15V charge pump that moves the pigment does not. Without it the panel
  initialises, reports BUSY correctly, and runs full refresh cycles that
  change nothing.
- **GPIO 18 cannot be shared.** It is I2S BCLK, fixed in the SoC's
  pinmux, and was mopidy-epaper's default PWR pin. Claiming it pulls the
  pin out of ALT0 and stops the audio without raising.
- **lgpio needs a writable working directory.** It creates a
  notification FIFO there, and systemd's default is `/`. When it fails,
  gpiozero walks its factory list down to the sysfs backend that modern
  kernels have dropped, and surfaces an `EINVAL` from three layers deep.
  Fixed with `WorkingDirectory=` and by naming the factory explicitly.
- **The packaged `mopidy.service` runs as a different user** against a
  different config, so enabling it unchanged starts a Mopidy that cannot
  read the extension or the library. Overridden with a drop-in.
- **`systemctl start` is not `systemctl enable`.** The daemon ran until
  the reboot and then did not. From the outside this is indistinguishable
  from dead buttons, because the panel keeps drawing either way.

All five are in the README now, which is the point of having found them.

## Setup script (2026-09-09)

`setup.sh` builds a player from a clean Raspberry Pi OS image: packages,
`config.txt`, group membership, both clones, the editable extension
install, `mopidy.conf`, both systemd units, and `enable`. `--verify`
re-runs the checks alone and changes nothing, which makes it a
diagnostic for a player that has stopped working as much as a
post-install test.

The checks are part of the script rather than a paragraph in the README
because every step it automates fails silently when missed. Checking
that a service is *enabled* and not merely running, that `epaper` is
among Mopidy's extensions, that GPIO 18 reads `a0`, that the DAC is
present and that `/epaper/status` answers covers every failure this
build has actually produced.

It derives the user and home from `SUDO_USER` rather than assuming
`murray`, prefers the checkout it is running from over a hardcoded path,
and generates paperpod's unit from the checked-in one by substituting
both in. Two deliberate refusals: it will not overwrite an existing
`mopidy.conf` (it checks for `pwr_pin =` and reports instead), and it
will not pull over an existing checkout.

**Verified on a clean image 2026-09-09.** All eight checks pass. The run
found two bugs that no amount of reading would have:

- A clean image has no `python3-pip`, so the extension install died. It
  had only ever worked by hand on a card that had pip from earlier work.
  Now installed, and invoked as `python3 -m pip`, since `sudo` resets
  PATH to `secure_path` and a bare `pip` is not reliably on it.
- The pending-reboot flag was per-run state, so re-running after an edit
  but before the reboot saw a correct `config.txt`, concluded nothing
  was pending, and started both services against a kernel with no
  overlay loaded. It now compares what `config.txt` asks for against
  what the running kernel has, which holds regardless of which run made
  the change. `--verify` leads with it, because a pending reboot makes
  every check below misreport.

Both are the same shape as the five failures under bring-up: nothing
raised, and the wrong state looked like the right one.

## Runs on battery (2026-09-10)

A PiSugar 3 under the Zero carries the whole build — Pi Zero 2 W, the DAC,
the panel, five buttons and wifi — with the USB lead out. It is a portable
player now, not a bench setup.

That was not obvious beforehand and the diagnosis went badly. Wifi failed to
associate when the battery first went on, everything else running. The
hypothesis was that the supply could not carry the wifi init spike on top of
the DAC and panel; the evidence appeared to support it, because an older card
booted with wifi fine on the same battery. That card had never had setup.sh
run on it, so it had no `hifiberry-dac` and no SPI — the DAC and panel were
idle, and it was never a fair comparison. Pulling the USB lead on the working
card settled it: the PiSugar carries the lot.

What actually ailed the other card is **unknown**. It was not the PiSugar and
not `config.txt`, which was read off the card and is clean. It was abandoned
rather than diagnosed. Worth remembering if a card misbehaves like that again.

Now that it runs on a measurable battery, today's CPU work has a number
attached to it at last: `pisugar-server` reports charge over I2C, so drain
rate converts straight to hours of runtime.

## The RTC is PiSugar's (2026-09-10)

Settled by taking the HAT+'s DS3231 off the bus — its I2C pins are
disconnected — so `0x57` and `0x68` are both PiSugar and there is nothing to
disambiguate. Its MCU-emulated RTC is register-compatible enough that the
stock `ds3231` driver binds to it, so `./setup.sh --rtc` works unchanged and
`/sys/class/rtc/rtc0` reads correctly.

That is the better of the two. PiSugar's RTC is backed by the main cell, so it
keeps time across a power cut; the HAT+'s never could, because nothing was
ever fitted to its JST connector. No cell to buy, and no shared address to
worry about.

Fifteen minutes went into a non-problem on the way, and the script was at
fault. The overlay was added at 23:34 to a machine that had booted at 23:12,
and overlays only apply at boot — but `--verify` reported a hard **FAIL**
saying the overlay was configured and the device unreadable, which reads as a
broken RTC rather than an unbooted one. `reboot_pending()` already knew this
pattern for the hifiberry overlay and for SPI; it simply did not know about
the RTC. It does now, and the check says "reboot" instead of failing.

Confirmed across a nine-hour power-off, on battery, with no cable attached
overnight. The evidence is worth spelling out because a single-line boot list
is not obviously proof:

    $ journalctl --list-boots
      0 ...  FIRST ENTRY Thu 2026-09-10 23:41:51 CEST
             LAST ENTRY  Fri 2026-09-11 08:48:52 CEST

    $ dmesg | grep -i rtc
    [   14.604] rtc-ds1307 1-0068: registered as rtc0
    [   14.605] rtc-ds1307 1-0068: setting system clock to 2026-09-11T06:33:21 UTC

One boot cannot begin at 23:41 and be at 08:33 fifteen seconds later. What
happened is that `fake-hwclock` restored the previous shutdown time, journald
stamped its first entries with it, and the RTC then corrected the clock
forward by nine hours. The chip could only know that by keeping time with the
Pi powered down, on PiSugar's cell.

NTP is not a confound here as long as the check is `dmesg` rather than `date`:
the kernel sets the clock from the RTC in early boot, long before networking
exists. `fake-hwclock` is the confound, and the answer is to leave the machine
off long enough that its stale value is obviously stale -- a two-minute power
cut would have looked identical either way.

## Power management (2026-09-11)

`pisugar-server` 2.3.4 is installed, for the clean shutdown rather than the
readout. Without it the device runs until the cell dies and stops mid-write,
which is how SD cards get damaged — and the card that mysteriously lost its
network was the one in use when the battery first went on. Never diagnosed, but
it fits.

Configured down to the minimum that provides it:

    OPTS=--config config.json --model 'PiSugar 3' --uds /tmp/pisugar-server.sock

No `--http`, `--ws` or `--tcp`, so there are no network listeners at all; the
web UI was the only unauthenticated one on the device. The safe shutdown is
internal to the daemon, so dropping the listeners does not weaken it.
`auto_shutdown_level` is 5% with a 30s delay.

Deliberately left off: `auto_rtc_sync`. The kernel's `rtc-ds1307` driver owns
the chip through the overlay, and letting the daemon sync it too would give
two writers to the same hardware with the kernel unaware — an occasional wrong
clock and no error anywhere.

Costs **0.79%** of a core, against their claimed "less than 2%". That makes it
the largest single consumer on the device now (paperpod ~0.20%, Mopidy ~0.64%),
which is a fair trade for not corrupting the card, and cheaper than
reimplementing shutdown logic to avoid a daemon.

Reading it, for whatever consumes this next:

    echo "get battery" | timeout 1 nc -U /tmp/pisugar-server.sock
    battery: 28.754677

The value jitters by around 1.5 points between reads seconds apart, because
the estimate comes from battery voltage, which sags under load and recovers.
Anything displaying it should smooth or round, or it will flicker between
numbers and look broken.

One note on installing it: their script fetches the .deb packages over plain
**http** and installs them as root with no signature check. The same files are
served over https, so the URLs were rewritten before running it.

## Backlog

- **Solder it onto a controller board.** Jumper wires for the panel, the
  DAC and five buttons is past what a breadboard should be asked to do.
  The five buttons share the ground at pin 30, so they need six wires
  rather than ten.
- **Battery on the panel.** The socket makes it easy now, and a portable
  player that cannot show its own charge is an odd thing. Smooth or round
  the value — raw readings jitter by a point or two and would flicker.
- **A case.**
- **Seek.** `seek_forward` / `seek_back` by 30s would fit naturally on a
  hold, and unlike volume there is no argument about where it belongs —
  it is Mopidy's, through JSON-RPC.

## Known limitations

- **Volume changes are heard about two seconds late.** The panel updates
  instantly, so the press, the JSON-RPC call and Mopidy's event push are all
  fast — the delay is entirely in the audio path, downstream of the
  softwaremixer applying gain. Audio already buffered plays out at the old
  volume first.

  `[audio] buffer_time = 300` in `mopidy.conf` does **not** fix it. The
  setting applied (confirmed in `mopidy config` output) and changed nothing,
  which fits: Mopidy's `buffer_time` largely governs buffering of streaming
  *sources*, not the playout path for a local file.

  Untried: `alsasink`'s own buffer, set on the output string as
  `alsasink device=... buffer-time=100000 latency-time=20000` — microseconds,
  not milliseconds. If the lag scales with that, it is the sink's buffer.
  If it does not, the buffering is upstream and no sink setting will reach it.

  Worth knowing before tuning it down: a smaller buffer means more frequent
  wakeups to refill, which works directly against the idle-power work. The
  goal is the largest buffer whose lag is tolerable, not the smallest that
  plays without underruns — and underruns sound far worse than a slow volume
  knob.

- **`/epaper/status` can 504 during a full refresh.** Holding 26 for
  `toggle_lock` repaints the panel, which holds the frontend actor for
  seconds; a poll landing in that window gets a 504 from `http.py` and
  logs a warning. Seen once, at 23:46:56 on 2026-09-09, four seconds
  after the lock. Harmless — the next poll two seconds later succeeds —
  but the status endpoint queueing behind a repaint is the actual cause,
  and raising `STATUS_TIMEOUT` would not fix it.

- **The mode guess can be briefly wrong.** `back` leaves the menu at the
  root and does not deeper in, and nothing here can tell which. It asks
  for an early poll rather than guessing, so there is a beat where a
  press could mean the wrong thing. Shortening the poll interval narrows
  the window at the cost of more requests.
- **A press logs one line, and only one.** Dispatch logs at info with the
  GPIO and the resolved command, so `journalctl -u paperpod -f` shows
  which button fired and what it meant. It does not show what Mopidy or
  the panel then did with it, so a press that logs but changes nothing
  still needs the other end checked.
- **No display of its own.** Everything paperpod does shows up on the
  panel or not at all, so a button that fires against a stopped Mopidy
  is invisible apart from a warning in the log.
