# AGENTS.md

Guidance for coding agents working in this repository. The [README](README.md)
is the build guide — hardware, wiring, config, installation. This covers what
it does not: the boundaries, the layering, and how work gets verified.

## What this is

The physical half of an e-paper Mopidy player. Buttons, volume and transport
for a Pi Zero 2 W with a Waveshare 2.13" panel and a PCM5100A I2S DAC. It is
**not** a Mopidy extension — it is a plain process that speaks HTTP to two
APIs that already exist.

## The boundary, and why it holds

Work that belongs elsewhere must go elsewhere. Two rules cover almost every
case:

1. **Anything about the panel** — the cursor, the menu, lock, wake — goes to
   `POST /epaper/input/<action>` in mopidy-epaper. Do not reimplement menu
   state here; the panel owns it, and a second copy would drift.
2. **Anything about playback** — volume, mute, transport, the tracklist —
   goes to Mopidy's JSON-RPC at `/mopidy/rpc`. Mopidy owns the mixer.

If a feature seems to need a new endpoint in mopidy-epaper, check first
whether Mopidy already exposes it. mopidy-epaper deliberately carries no
volume or transport actions, and that decision is recorded in its
`PROGRESS.md` — reopening it needs a reason, not convenience.

## Where verification happens

**Do not run Python, `pytest`, or virtualenv setup on the development Mac.**
The target is the Pi, and that is where anything is confirmed. Write the code,
say plainly that it is unrun, and hand it over.

The Pi is `pi0`, user `murray`. Like mopidy-epaper, this runs from a git
checkout — merging a PR does not update it, so a change needs `git pull` there
before it takes effect.

Tests are `pytest tests/`, run on the Pi.

## Layering

```
app.py        GPIO wiring and the process — the only module touching libgpiod
   ↓
bindings.py   what each button means, as data; no I/O
mode.py       which screen the panel is showing, cached
   ↓
player.py     the two HTTP APIs, behind one small surface
```

Two rules hold this up:

1. **Only `app.py` touches the GPIO.** Everything else must be exercisable on
   any machine, which is what makes the button mapping testable without pins.
2. **`bindings.py` is data, not behaviour.** `BUTTONS` and `COMMANDS` are
   plain dicts. Logic that inspects a press belongs in `mode.py` or `app.py`.

## Two failure modes worth designing against

Both are silent, which is what makes them expensive.

**Pin collisions.** GPIO 18, 19, 20 and 21 are I2S; 7–11 are SPI; 17, 24 and
25 are the panel; 2 and 3 are I2C. Claiming one of those from libgpiod does
not raise — it takes the pin out of ALT0 and the other device simply stops
working. `tests/test_bindings.py` checks the button map against that list;
keep it current if the hardware changes.

**A wrong mode guess.** If `mode.py` thinks the menu is open when it is not, a
button does something visible and unasked-for — the worst failure a physical
control can have. Only apply a guess where the outcome is certain (navigation
from now-playing always opens the menu). Where it is not, ask for an early
poll instead. Never track menu state independently of the panel.

## Style

Comments explain *why*, not what. Match the surrounding density, which is
deliberate. When a decision looks arbitrary from the code alone — a guess that
is only applied in one direction, a poll interval, a read-before-write — the
reason belongs next to it.

Python targets 3.9+. No formatter or linter is configured; follow the
surrounding code. Runtime dependencies are the standard library plus libgpiod;
adding a third is a decision, not a detail.

## Documentation to keep current

- **`PROGRESS.md`** — the design log. Every change gets an entry explaining the
  reasoning, dated, and marked **unverified** until it has run on the Pi. When
  the user confirms something on hardware, record that against the entry.
  Rejected options and known limitations live here too.
- **`README.md`** — the build guide: wiring, config, installation, the button
  map. Anything someone assembling one of these needs.

## Git

Branch per change, merged to `main` via PR. Commit subjects are imperative and
under 50 characters. No `Co-Authored-By` or Claude Code footer in commits or
PR bodies.
