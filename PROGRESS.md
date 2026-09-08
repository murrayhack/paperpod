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

## Backlog

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
