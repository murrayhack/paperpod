# paperpod

The physical half of an e-paper Mopidy player: a Raspberry Pi Zero with a
Waveshare 2.13" panel, a PCM5100A I2S DAC and five buttons, in a case, behaving
like an MP3 player rather than a computer that plays music.

The screen itself is
[mopidy-epaper](https://github.com/murrayhack/mopidy-epaper), a Mopidy
extension. This repository is everything around it — the buttons, the DAC, the
wiring, the service units and the build.

## How the pieces divide

paperpod is not a Mopidy extension. It is an ordinary process that talks HTTP
to two APIs that already exist:

| What | Where it goes | Why |
| --- | --- | --- |
| Cursor, menu, lock, wake | `POST /epaper/input/<action>` | The panel is mopidy-epaper's |
| Volume, mute, transport | `POST /mopidy/rpc` | The mixer and tracklist are Mopidy's |

That split is deliberate on both sides. mopidy-epaper exposes no volume
actions, because Mopidy already owns the mixer and a second route to it would
mean two ways to do one thing. The panel still *displays* volume — it redraws
on Mopidy's `volume_changed` event — so it shows a value it does not control,
which is the right way round.

The practical benefit is that nothing here is load-bearing for the screen. Rip
the buttons out and the panel still works, driven by the web remote at
`http://<pi>:6680/epaper/`.

## Hardware

- Raspberry Pi Zero 2 W
- Waveshare 2.13" e-Paper HAT, **V4** (250×122)
- PCM5100A I2S DAC breakout
- 5 momentary buttons
- A DS3231 RTC on I2C (optional)

The panel and the DAC both want the 40-pin header, so the panel is wired off
it with jumpers.

### Pin map

Everything, in one table, because the whole point of a build like this is that
two devices are competing for one header.

| Device | Signal | BCM | Physical |
| --- | --- | --- | --- |
| Panel | 3.3V | — | 1 |
| Panel | **5V** | — | 2 |
| Panel | GND | — | 6, 9 |
| Panel | RST | 17 | 11 |
| Panel | BUSY | 24 | 18 |
| Panel | MOSI | 10 | 19 |
| Panel | DC | 25 | 22 |
| Panel | SCLK | 11 | 23 |
| Panel | CS | 8 | 24 |
| DAC | VIN | — | 4 (5V) |
| DAC | GND | — | 14 |
| DAC | BCK | 18 | 12 |
| DAC | WS | 19 | 35 |
| DAC | DIN | 21 | 40 |
| DAC | MC/SCK | — | GND (39) |
| RTC | SDA / SCL | 2, 3 | 3, 5 |
| Buttons | up, down, select, back, menu | 5, 6, 13, 16, 26 | 29, 31, 33, 36, 37 |

### The header, as it sits

The same thing laid out physically, which is the view you want when
soldering — free pins and collisions are visible at a glance rather than
cross-referenced. Pin 1 is the corner nearest the SD card.

```
                                 ┌───────┐
             panel  3.3V  ─────  │  1  2 │  ─────  5V     panel  *
             RTC    SDA   ─────  │  3  4 │  ─────  5V     DAC VIN
             RTC    SCL   ─────  │  5  6 │  ─────  GND    panel
                    free  ─────  │  7  8 │  ─────  free
             panel  GND   ─────  │  9 10 │  ─────  free
             panel  RST   ─────  │ 11 12 │  ─────  BCK    DAC     *
                    free  ─────  │ 13 14 │  ─────  GND    DAC
                    free  ─────  │ 15 16 │  ─────  free
                    free  ─────  │ 17 18 │  ─────  BUSY   panel
             panel  MOSI  ─────  │ 19 20 │  ─────  free
              SPI   MISO  ─────  │ 21 22 │  ─────  DC     panel
             panel  SCLK  ─────  │ 23 24 │  ─────  CS     panel
                    free  ─────  │ 25 26 │  ─────  CE1    SPI
                  EEPROM  ─────  │ 27 28 │  ─────  EEPROM
            button  up    ─────  │ 29 30 │  ─────  GND    buttons
            button  down  ─────  │ 31 32 │  ─────  free
            button  select─────  │ 33 34 │  ─────  free
             DAC    WS    ─────  │ 35 36 │  ─────  back   button
            button  menu  ─────  │ 37 38 │  ─────  PCM_DIN  (I2S)
             DAC    GND   ─────  │ 39 40 │  ─────  DIN    DAC
                                 └───────┘
```

`*` marks the two that fail silently if you forget them: pin 2, which feeds
the panel's charge pump, and pin 12, which is I2S BCLK and must not be given
to the panel's PWR.

**Free to use:** 7, 8, 10, 13, 15, 16, 20, 25, 32, 34 — plus 17 (3.3V) if the
RTC is a separate module and needs power. Leave 21 and 26 alone (SPI MISO and
CE1), 27 and 28 alone (HAT EEPROM), and 38 alone (I2S claims it with the
overlay loaded, even though nothing is wired to it).

The five buttons share the single ground at pin 30, so they need six wires
between them rather than ten.

Two things here cost real time to discover, so they are worth stating plainly:

**The panel needs 5V as well as 3.3V.** 3.3V runs the controller, but the
±15V charge pump that moves the pigment does not. Without 5V the panel
initialises cleanly, reports ready, and runs full refresh cycles that change
nothing at all. Nothing logs a warning, because nothing is wrong as far as the
controller knows.

**GPIO 18 belongs to the DAC.** It is I2S BCLK, fixed in the SoC's pinmux, and
it is also mopidy-epaper's default PWR pin. Set `pwr_pin =` in `mopidy.conf`
(see below) or the extension claims it when it starts, pulls it out of ALT0,
and the audio goes quiet with nothing in any log.

## Software

### config.txt

`/boot/firmware/config.txt`:

```ini
dtparam=spi=on
dtparam=i2c_arm=on

# The onboard device would otherwise compete to be card 0.
#dtparam=audio=on
dtoverlay=hifiberry-dac
```

`hifiberry-dac` is the right overlay for the PCM510xA family — hardware
configured, no I2C control. After a reboot, `aplay -l` should show
`snd_rpi_hifiberry_dac`.

### mopidy.conf

```ini
[audio]
output = alsasink device=sysdefault:CARD=sndrpihifiberry

[epaper]
enabled = true
# GPIO 18 is I2S BCLK here, so the panel must not claim it.
pwr_pin =
```

Leave the mixer alone. The PCM5100A has no hardware volume control, so
Mopidy's default `softwaremixer` is what makes volume work at all — and what
keeps the panel's volume readout meaningful. Guides that tell you to set
`mixer = alsamixer` are written for chips like the PCM5122, which has an
I2C-controlled volume this one lacks.

### Installing

Clone it beside mopidy-epaper — `/home/murray/paperpod` is what the service
unit expects — and let apt supply `gpiozero`:

```sh
sudo apt install -y python3-gpiozero python3-pytest
pytest tests/
```

No install step is needed. The service runs from the checkout, and the tests
put the root on the path themselves, so a `git pull` is the whole update
procedure. `sudo pip install --break-system-packages --no-deps -e .` is
optional and only buys you a `paperpod` command on the path.

While working out the button layout, run it in the foreground — the service
logs nothing on a press, because dispatch is a debug-level line:

```sh
python3 -m paperpod.app --verbose
```

## Running on boot

Two services, and Mopidy's needs adjusting before either will work.

### Mopidy

The apt package ships its own `mopidy.service`, which runs as the **`mopidy`
user** with config at **`/etc/mopidy/mopidy.conf`**. Neither is what you get
running `mopidy` by hand, so enabling it unchanged starts a Mopidy that cannot
read your config, cannot read your home directory, and has never scanned your
library.

Rather than migrate all that, override the unit to run as you. A drop-in, so
package upgrades leave it alone and the unit keeps its name — which matters,
because paperpod orders itself after `mopidy.service`:

```sh
sudo mkdir -p /etc/systemd/system/mopidy.service.d
sudo tee /etc/systemd/system/mopidy.service.d/override.conf >/dev/null <<'EOF'
[Service]
User=murray
SupplementaryGroups=audio gpio spi
WorkingDirectory=/home/murray
Environment=GPIOZERO_PIN_FACTORY=lgpio
ExecStart=
ExecStart=/usr/bin/mopidy --config /home/murray/.config/mopidy/mopidy.conf
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now mopidy
```

Four of those lines are load-bearing in ways that are not obvious:

- **The empty `ExecStart=`** clears the packaged command before setting yours.
  Without it systemd rejects the unit outright.
- **`WorkingDirectory=/home/murray`** because lgpio creates a notification
  FIFO in the working directory, and systemd's default is `/`, which the
  service user cannot write. lgpio then fails, gpiozero falls through its
  factory list to the sysfs backend that modern kernels have dropped, and the
  panel dies with an `EINVAL` from deep inside gpiozero. Running by hand hides
  this, because your shell's working directory is writable.
- **`GPIOZERO_PIN_FACTORY=lgpio`** so that fallback cannot happen quietly
  again. If lgpio will not start you get an error naming it, instead of a
  confusing traceback about `/sys/class/gpio`.
- **Dropping `/usr/share/mopidy/conf.d`** from the config path is deliberate.
  Running `mopidy` by hand never loaded it either, so this keeps the service
  identical to what you tested interactively.

Check it took — `systemctl cat mopidy` should show the override at the bottom,
and the log should read `Loading config from file:///home/murray/.config/...`
with `epaper` among the enabled extensions.

### paperpod

```sh
sudo cp systemd/paperpod.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now paperpod
journalctl -u paperpod -f
```

**`enable`, not just `start`.** `start` runs it now and it is gone after a
reboot; `enable` is what writes the boot symlink. `systemctl is-enabled
paperpod` is the one-word check, and worth running before concluding the
buttons are broken — a missing daemon and a bad ground look identical from the
outside, while the panel keeps drawing either way because that is Mopidy's job.

Then reboot and confirm both come back:

```sh
sudo reboot
systemctl is-enabled mopidy paperpod
systemctl status mopidy paperpod --no-pager
```

## Buttons

Five buttons, and each means two things depending on what the panel is
showing:

| GPIO | In the menu | Now playing | Held |
| --- | --- | --- | --- |
| 5 | up | volume up | — |
| 6 | down | volume down | — |
| 13 | select | play / pause | next track |
| 16 | back | previous track | — |
| 26 | home | home | lock |

GPIO 26 is `home` in both modes on purpose: whatever is on screen, one button
always gets you to the menu and out of it again.

The mapping lives in `paperpod/bindings.py` as plain data. Change the pins
there — a test checks your choices against everything already spoken for (I2S,
SPI, the panel, I2C), because a collision takes a device quiet rather than
raising.

### How it knows which mode it is in

The panel is the authority: it opens the menu when a navigation action arrives
and closes it on its own after `menu_timeout`. `GET /epaper/status` reports
`in_menu`, but it waits on the frontend actor, which a full refresh can hold
for a second or two on a Pi Zero. Asking per press would put that in front of
every button.

So `paperpod/mode.py` polls it on a timer and caches the answer, and applies
the one case that is certain — a navigation action from the now-playing screen
always opens the menu — without waiting. Presses that *might* close the menu
ask for an early poll instead of guessing, because guessing wrong in that
direction makes the next button do something visible and unasked-for.

## Development

Only `paperpod/app.py` imports gpiozero, so the bindings, the mode cache and
the HTTP client can be exercised anywhere:

```sh
pytest tests/
```

## License

MIT — see [LICENSE](LICENSE).
