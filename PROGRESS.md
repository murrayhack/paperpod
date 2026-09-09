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
| Runtime dependencies | Standard library only, plus gpiozero | Both APIs are plain HTTP, so urllib is enough; nothing to keep current |
| Where navigation goes | `POST /epaper/input/<action>` | The panel owns its menu state; a second copy here would drift |
| Where volume and transport go | Mopidy's JSON-RPC | Mopidy owns the mixer and the tracklist |
| Button mapping | Plain data in `bindings.py` | Readable, changeable and testable without a Pi |
| gpiozero imports | `app.py` only | Everything else runs on any machine, which is what makes the mapping testable |
| Knowing which screen is showing | Poll `/epaper/status` on a timer and cache it | It waits on the frontend actor, which a full refresh holds for a second or two — asking per press would put that in front of every button |
| Guessing the mode ahead of the poll | Only where certain | Navigation from now-playing always opens the menu; `back` leaves it only at the root, and guessing wrong makes a button do something visible and unasked-for |
| Volume steps | Read the current volume, then write | A local counter would drift from the web remote and other clients |

## Verified on hardware (2026-09-09)

Pi Zero 2 W, Raspberry Pi OS Trixie (Python 3.13), Mopidy 3.4.2, real
panel and a PCM5100A on `hifiberry-dac`.

- [x] 23 tests pass.
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

Not yet exercised: volume on 5 and 6, `back` / `previous_track` on 16,
the menu button on 26, holding 26 for lock, and the mode switch between
the menu and now-playing. The mapping is tested, the two ends are
tested, but most individual buttons have not been pressed.

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

## Backlog

- **Press the other four buttons.** Only GPIO 13 has been exercised —
  tap for play/pause, hold for next. Still untried: volume on 5 and 6,
  `back` and `previous_track` on 16, the menu button on 26 and its
  hold-to-lock, and the mode switch itself (open the menu, confirm 5 and
  6 move the cursor rather than change volume, wait out `menu_timeout`,
  confirm they go back to volume). Worth doing on jumpers, since a
  mapping bug is far easier to fix before anything is soldered.
- **Solder it onto a controller board.** Jumper wires for the panel, the
  DAC and five buttons is past what a breadboard should be asked to do.
  The five buttons share the ground at pin 30, so they need six wires
  rather than ten.
- **A case.**
- **Seek.** `seek_forward` / `seek_back` by 30s would fit naturally on a
  hold, and unlike volume there is no argument about where it belongs —
  it is Mopidy's, through JSON-RPC.

## Known limitations

- **The mode guess can be briefly wrong.** `back` leaves the menu at the
  root and does not deeper in, and nothing here can tell which. It asks
  for an early poll rather than guessing, so there is a beat where a
  press could mean the wrong thing. Shortening the poll interval narrows
  the window at the cost of more requests.
- **The service logs nothing on a press.** Dispatch is a debug line and
  the unit does not pass `--verbose`, so a working button and a dead one
  look identical in the journal. Run it in the foreground to debug.
- **No display of its own.** Everything paperpod does shows up on the
  panel or not at all, so a button that fires against a stopped Mopidy
  is invisible apart from a warning in the log.
