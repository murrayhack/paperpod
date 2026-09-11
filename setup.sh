#!/usr/bin/env bash
#
# Set up a paperpod from a clean Raspberry Pi OS image.
#
# Everything here was learned the hard way, and almost all of it fails
# silently when missed: a panel with no 5V initialises and draws nothing, a
# PWR pin left on GPIO 18 kills the audio without raising, lgpio in an
# unwritable working directory sends mopidy-epaper's gpiozero down to a sysfs
# backend the kernel dropped, a config.txt line appended below a conditional
# filter applies to a board this is not, and `systemctl start` without
# `enable` works perfectly until the first reboot. So the script does the
# install and then checks its work.
#
# From a clean image:
#
#   sudo apt update && sudo apt install -y git
#   git clone https://github.com/murrayhack/paperpod.git ~/paperpod
#   cd ~/paperpod
#   sudo ./setup.sh            install and configure
#   sudo reboot
#   ./setup.sh --verify        check an existing install, change nothing
#
# Add --rtc to use the DS3231 on the 2.13" e-Paper HAT+. Off by default: the
# plain HAT has no RTC, and the overlay would bind to nothing. Keeping time
# across a power cut also needs a cell on the board's JST connector.
#
# Safe to re-run. It will not overwrite an existing mopidy.conf or pull over a
# checkout you may have edited; it reports what is missing instead.

set -euo pipefail

EPAPER_REPO=https://github.com/murrayhack/mopidy-epaper.git
PAPERPOD_REPO=https://github.com/murrayhack/paperpod.git

PROBLEMS=0
WANT_RTC=0

# Overridable so reboot_pending can be exercised without a Pi.
RTC_SYSFS=${RTC_SYSFS:-/sys/class/rtc/rtc0}

# ---------------------------------------------------------------- output ---

BOLD=$(tput bold 2>/dev/null || true)
RESET=$(tput sgr0 2>/dev/null || true)

step() { printf '\n%s==> %s%s\n' "$BOLD" "$1" "$RESET"; }
info() { printf '    %s\n' "$1"; }
ok()   { printf '    ok    %s\n' "$1"; }
bad()  { printf '    FAIL  %s\n' "$1"; PROBLEMS=$((PROBLEMS + 1)); }
warn() { printf '    note  %s\n' "$1"; }
die()  { printf '\nerror: %s\n' "$1" >&2; exit 1; }

# ------------------------------------------------------------ who and where ---

# Run under sudo, but clone and configure as the human. SUDO_USER is who
# invoked it; falling back to the current user covers running --verify plainly.
TARGET_USER=${SUDO_USER:-$(id -un)}
if [ "$TARGET_USER" = root ]; then
    die "run this with sudo as a normal user, not as root"
fi
TARGET_HOME=$(getent passwd "$TARGET_USER" | cut -d: -f6)
if [ ! -d "$TARGET_HOME" ]; then
    die "no home directory for $TARGET_USER"
fi

EPAPER_DIR="$TARGET_HOME/mopidy-epaper"
MOPIDY_CONF="$TARGET_HOME/.config/mopidy/mopidy.conf"

# Prefer the checkout this script is running from, so a clone somewhere other
# than the default does not end up with a second copy in the home directory
# and a service unit pointing at the wrong one. Falls back to the default,
# which is what happens when only setup.sh has been downloaded — the clone
# step below then fetches the rest.
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
if [ -f "$SCRIPT_DIR/paperpod/app.py" ]; then
    PAPERPOD_DIR="$SCRIPT_DIR"
else
    PAPERPOD_DIR="$TARGET_HOME/paperpod"
fi

# Bookworm and later moved the boot partition. Support both rather than
# guessing wrong and silently editing a file nothing reads.
if [ -f /boot/firmware/config.txt ]; then
    CONFIG_TXT=/boot/firmware/config.txt
elif [ -f /boot/config.txt ]; then
    CONFIG_TXT=/boot/config.txt
else
    CONFIG_TXT=
fi

as_user() { sudo -u "$TARGET_USER" -H "$@"; }

# Is the running kernel missing something config.txt asks for?
#
# Asked this way round rather than by tracking whether this run edited the
# file: re-running the script after an edit but before the reboot would
# otherwise see a correct config.txt and conclude nothing was pending.
reboot_pending() {
    [ -n "$CONFIG_TXT" ] || return 1
    if grep -qE '^dtoverlay=hifiberry-dac' "$CONFIG_TXT" \
        && ! aplay -l 2>/dev/null | grep -q sndrpihifiberry; then
        return 0
    fi
    if grep -qE '^dtparam=spi=on' "$CONFIG_TXT" && [ ! -e /dev/spidev0.0 ]; then
        return 0
    fi
    # Any i2c-rtc overlay, not just ds3231: the question is whether config.txt
    # asks for an RTC the running kernel has not been given, and the chip does
    # not change the answer.
    if grep -qE '^dtoverlay=i2c-rtc,' "$CONFIG_TXT" && [ ! -e "$RTC_SYSFS" ]; then
        return 0
    fi
    return 1
}

# ------------------------------------------------------------------ steps ---

install_packages() {
    step "Installing packages"
    apt-get update -qq
    # Mopidy from apt lives in the system Python, so its extensions must too.
    # Letting apt supply the dependencies gives pip nothing to resolve, which
    # matters on a Pi Zero: pip may otherwise rebuild Pillow from source.
    #
    # Both GPIO libraries are needed, for different consumers. paperpod reads
    # button edges through libgpiod. mopidy-epaper still claims the panel's
    # RST and DC pins with gpiozero, which needs lgpio under it.
    apt-get install -y -qq \
        git mopidy mopidy-local \
        python3-pil python3-pykka python3-spidev python3-libgpiod \
        python3-gpiozero python3-lgpio \
        python3-pip python3-pytest fonts-dejavu-core alsa-utils curl ffmpeg \
        i2c-tools
    ok "packages installed"
}

# config.txt applies each line under the last conditional filter above it, so a
# line appended to a file ending in [pi5], [cm4] or [none] applies to nothing on
# this board. Worse, grep still finds it, so the next run reports it as already
# set while it does nothing at all. Reset to unconditional before appending.
#
# Idempotent: after the first append the trailing filter is [all], so later
# calls add nothing. Only called when something is actually being appended, to
# avoid writing [all] into a file that needed no changes.
append_config() {
    local last
    last=$(grep -oE '^\[[^]]+\]' "$CONFIG_TXT" | tail -1 || true)
    if [ -n "$last" ] && [ "$last" != "[all]" ]; then
        printf '\n[all]\n' >> "$CONFIG_TXT"
        info "added [all] — config.txt ended inside a $last section"
    fi
    printf '%s\n' "$1" >> "$CONFIG_TXT"
}

configure_boot() {
    step "Configuring $CONFIG_TXT"
    [ -n "$CONFIG_TXT" ] || die "no config.txt found; is this a Raspberry Pi?"

    local changed=0

    enable_param() {
        local param=$1
        if grep -qE "^${param}$" "$CONFIG_TXT"; then
            ok "$param already set"
        elif grep -qE "^#\s*${param}$" "$CONFIG_TXT"; then
            sed -i "s|^#\s*${param}$|${param}|" "$CONFIG_TXT"
            info "uncommented $param"
            changed=1
        else
            append_config "$param"
            info "added $param"
            changed=1
        fi
    }

    enable_param "dtparam=spi=on"
    enable_param "dtparam=i2c_arm=on"

    # The onboard device would otherwise compete with the DAC to be card 0.
    if grep -qE '^dtparam=audio=on' "$CONFIG_TXT"; then
        sed -i 's|^dtparam=audio=on|#dtparam=audio=on|' "$CONFIG_TXT"
        info "commented out dtparam=audio=on"
        changed=1
    else
        ok "onboard audio already off"
    fi

    # hifiberry-dac is the overlay for the PCM510xA family: hardware
    # configured, no I2C control. It brings up the I2S node itself, so
    # dtparam=i2s stays out of it.
    if grep -qE '^dtoverlay=hifiberry-dac' "$CONFIG_TXT"; then
        ok "hifiberry-dac overlay already present"
    else
        append_config 'dtoverlay=hifiberry-dac'
        info "added dtoverlay=hifiberry-dac"
        changed=1
    fi

    # Only the HAT+ carries a DS3231; on the plain HAT this overlay binds to
    # nothing, so it is opt-in rather than assumed.
    if [ "$WANT_RTC" = 1 ]; then
        if grep -qE '^dtoverlay=i2c-rtc,ds3231' "$CONFIG_TXT"; then
            ok "ds3231 overlay already present"
        else
            append_config 'dtoverlay=i2c-rtc,ds3231'
            info "added dtoverlay=i2c-rtc,ds3231"
            changed=1
        fi
    fi

    if [ "$changed" = 1 ]; then
        warn "boot config changed — a reboot is needed before this takes effect"
    fi
}

# Is the DS3231 answering on the main I2C bus? Reads the 0x60 row of
# i2cdetect, where 0x68 is the tenth field: "68" is the chip unclaimed, "UU" is
# the kernel's RTC driver already bound to it.
ds3231_present() {
    command -v i2cdetect >/dev/null || return 1
    i2cdetect -y 1 2>/dev/null \
        | awk '/^60:/ { exit ($10 == "68" || $10 == "UU") ? 0 : 1 }'
}

configure_rtc() {
    [ "$WANT_RTC" = 1 ] || return 0
    step "Real-time clock"

    # dtparam=i2c_arm=on brings up the controller, but /dev/i2c-* only appears
    # once this module is loaded -- which is what i2cdetect talks to. The
    # kernel's RTC driver binds straight to the chip and does not need it, so
    # this is purely so the bus can be inspected.
    if [ -f /etc/modules-load.d/i2c-dev.conf ]; then
        ok "i2c-dev loads at boot"
    else
        printf 'i2c-dev\n' > /etc/modules-load.d/i2c-dev.conf
        info "wrote /etc/modules-load.d/i2c-dev.conf"
    fi
    modprobe i2c-dev 2>/dev/null || warn "could not load i2c-dev now; a reboot will"

    if [ -e /sys/class/rtc/rtc0/time ]; then
        ok "RTC bound as $(cat /sys/class/rtc/rtc0/name 2>/dev/null || echo rtc0)"
        # Seed it from system time, which NTP has usually corrected by now. An
        # unseeded DS3231 reads back an arbitrary date and looks broken.
        if hwclock -w 2>/dev/null; then
            ok "wrote system time to the RTC"
        else
            warn "could not write to the RTC (hwclock -w)"
        fi
    elif ds3231_present; then
        info "DS3231 answering at 0x68 — /dev/rtc0 appears after a reboot"
    else
        warn "no DS3231 at 0x68: is this the HAT+ rather than the plain HAT?"
    fi

    warn "the RTC only keeps time unpowered if a cell is fitted to the"
    warn "board's JST connector — without one it resets on every power cut"
}

add_groups() {
    step "Group membership"
    # Stock Raspberry Pi OS puts the first user in these already, but a fresh
    # image with a different account may not be, and the services fail
    # confusingly without them.
    for group in gpio spi i2c audio; do
        if getent group "$group" >/dev/null; then
            if id -nG "$TARGET_USER" | tr ' ' '\n' | grep -qx "$group"; then
                ok "$TARGET_USER already in $group"
            else
                usermod -aG "$group" "$TARGET_USER"
                info "added $TARGET_USER to $group"
            fi
        fi
    done
}

clone_repos() {
    step "Fetching the code"
    clone_one() {
        local url=$1 dir=$2
        if [ -d "$dir/.git" ]; then
            # Deliberately not pulling: the checkout may have local work, and
            # a surprise merge is a worse outcome than a stale copy.
            ok "$(basename "$dir") already cloned — pull it yourself if stale"
        else
            as_user git clone --quiet "$url" "$dir"
            info "cloned $(basename "$dir")"
        fi
    }
    clone_one "$EPAPER_REPO" "$EPAPER_DIR"
    clone_one "$PAPERPOD_REPO" "$PAPERPOD_DIR"
}

install_extension() {
    step "Installing mopidy-epaper"
    # --no-deps because apt already supplied them; without it pip may decide
    # the apt Pillow does not satisfy the pin and rebuild it from source,
    # which takes a very long time on a Pi Zero and shadows the apt version.
    # Editable, so a git pull is the whole update procedure.
    #
    # Invoked as `python3 -m pip` rather than `pip`: sudo resets PATH to
    # secure_path, and a bare `pip` is not reliably on it.
    # Installing into the system Python as root is the point here, not an
    # accident, so pip's warning about it is noise.
    python3 -m pip install --break-system-packages --no-deps \
        --root-user-action=ignore --quiet -e "$EPAPER_DIR"
    ok "installed editable from $EPAPER_DIR"

    # Mopidy-Local 3.2.1 imports imghdr, which PEP 594 removed from the stdlib
    # in Python 3.13. Without it the extension raises on load, Mopidy drops it,
    # and `mopidy local scan` fails with the un-obvious "unrecognized command:
    # local". Harmless to install where imghdr still exists.
    if ! python3 -c 'import imghdr' 2>/dev/null; then
        python3 -m pip install --break-system-packages \
            --root-user-action=ignore --quiet standard-imghdr
        ok "installed the imghdr backport for mopidy-local"
    fi
    # paperpod needs no install: its unit runs from the checkout.
}

write_mopidy_conf() {
    step "Mopidy configuration"
    if [ -f "$MOPIDY_CONF" ]; then
        ok "$MOPIDY_CONF exists — leaving it alone"
        grep -qE '^\s*pwr_pin\s*=\s*$' "$MOPIDY_CONF" \
            || bad "add 'pwr_pin =' under [epaper], or the panel claims GPIO 18 from I2S"
        grep -qE '^\s*output\s*=.*sndrpihifiberry' "$MOPIDY_CONF" \
            || warn "no hifiberry output set under [audio]; autoaudiosink may pick HDMI"
        return
    fi

    as_user mkdir -p "$(dirname "$MOPIDY_CONF")"
    as_user tee "$MOPIDY_CONF" >/dev/null <<EOF
[audio]
output = alsasink device=sysdefault:CARD=sndrpihifiberry

[local]
media_dir = $TARGET_HOME/music

[epaper]
enabled = true
# GPIO 18 is I2S BCLK on this build, so the panel must not claim it for PWR.
pwr_pin =
EOF
    info "wrote $MOPIDY_CONF"
    warn "leave the mixer alone — the PCM5100A has no hardware volume, so"
    warn "Mopidy's default softwaremixer is what makes volume work at all"
}

seed_music() {
    step "Music library"
    local music_dir="$TARGET_HOME/music"

    if [ ! -d "$music_dir" ]; then
        as_user mkdir -p "$music_dir"
        info "created $music_dir"
    else
        ok "$music_dir exists"
    fi

    # Generate a test file rather than fetching one: no licensing question, no
    # dependency on a URL outlasting the script. Tagged, because the panel's
    # whole job is rendering metadata, and three minutes long so the progress
    # bar has somewhere to go. Quiet on purpose — it is a sine wave.
    local test_file="$music_dir/paperpod-test-tone.mp3"
    if [ -e "$test_file" ]; then
        ok "test tone already present"
    elif ! find "$music_dir" -type f \
            \( -iname '*.mp3' -o -iname '*.flac' -o -iname '*.ogg' -o -iname '*.m4a' \) \
            -print -quit | grep -q .; then
        if command -v ffmpeg >/dev/null; then
            as_user ffmpeg -loglevel error -f lavfi \
                -i "sine=frequency=440:duration=180" \
                -filter:a "volume=0.1" \
                -metadata title="Test Tone" \
                -metadata artist="paperpod" \
                -metadata album="Setup" \
                "$test_file"
            info "wrote a tagged 3-minute test tone"
        else
            warn "ffmpeg not installed, so no test tone — add your own music"
        fi
    else
        ok "music already in $music_dir"
    fi

    if reboot_pending; then
        warn "skipping the library scan until after the reboot"
        return
    fi

    # Not piped through `tail -1`: a failing scan reports the reason over many
    # lines and the last one is the least useful of them. Show the summary on
    # success, the whole thing on failure.
    local scan_log
    scan_log=$(as_user mopidy --config "$MOPIDY_CONF" local scan 2>&1) || {
        bad "library scan failed"
        printf '%s\n' "$scan_log" | sed 's/^/    /'
        return
    }
    printf '%s\n' "$scan_log" | tail -1 | sed 's/^/    /'
}

write_units() {
    step "systemd units"

    # The packaged mopidy.service runs as the mopidy user against
    # /etc/mopidy/mopidy.conf. A drop-in rather than a replacement, so package
    # upgrades leave it alone and the unit keeps its name — paperpod orders
    # itself after mopidy.service.
    mkdir -p /etc/systemd/system/mopidy.service.d
    tee /etc/systemd/system/mopidy.service.d/override.conf >/dev/null <<EOF
[Service]
User=$TARGET_USER
SupplementaryGroups=audio gpio spi
# lgpio puts a notification FIFO in the working directory, and systemd's
# default of / is not writable by the service user. When lgpio fails gpiozero
# falls through to the sysfs backend modern kernels have dropped, and the
# panel dies with an EINVAL from deep inside gpiozero.
WorkingDirectory=$TARGET_HOME
# So that fallback cannot happen quietly again.
Environment=GPIOZERO_PIN_FACTORY=lgpio
# The empty assignment clears the packaged command; systemd rejects the unit
# without it.
ExecStart=
ExecStart=/usr/bin/mopidy --config $MOPIDY_CONF
EOF
    ok "wrote the mopidy.service override"

    # Generated from the shipped unit so the user and paths match this machine.
    sed -e "s|^User=.*|User=$TARGET_USER|" \
        -e "s|^WorkingDirectory=.*|WorkingDirectory=$PAPERPOD_DIR|" \
        "$PAPERPOD_DIR/systemd/paperpod.service" \
        > /etc/systemd/system/paperpod.service
    ok "wrote paperpod.service"

    systemctl daemon-reload
}

enable_services() {
    step "Enabling services"
    # enable, not start: start runs it now and it is gone after a reboot.
    systemctl enable --quiet mopidy paperpod
    ok "both enabled at boot"

    if reboot_pending; then
        warn "not starting them — the kernel does not have the overlay yet"
    else
        systemctl restart mopidy paperpod
        ok "both started"
    fi
}

# ----------------------------------------------------------------- verify ---

verify() {
    step "Checking the install"

    if reboot_pending; then
        bad "reboot pending — config.txt asks for more than the running kernel has"
        info "everything below will misreport until you reboot"
    fi

    for unit in mopidy paperpod; do
        if [ "$(systemctl is-enabled "$unit" 2>/dev/null || true)" = enabled ]; then
            ok "$unit enabled at boot"
        else
            bad "$unit is not enabled — it will not come back after a reboot"
        fi
        if systemctl is-active --quiet "$unit"; then
            ok "$unit running"
        else
            bad "$unit is not running (journalctl -u $unit -b)"
        fi
    done

    # Take the *last* extensions line rather than any of them: an earlier,
    # healthier start in the same boot would otherwise pass the check while the
    # running process is broken. And wait for it -- systemctl returns as soon as
    # the process execs, but Mopidy needs a while on a Pi Zero to get as far as
    # logging its extensions, so an immediate check races the startup it means
    # to be checking.
    local epaper_seen=0
    for _ in $(seq 30); do
        if journalctl -u mopidy -b --no-pager 2>/dev/null \
            | grep 'Enabled extensions:' | tail -1 | grep -q 'epaper'; then
            epaper_seen=1
            break
        fi
        sleep 1
    done
    if [ "$epaper_seen" -eq 1 ]; then
        ok "Mopidy loaded the epaper extension"
    else
        bad "epaper not among Mopidy's enabled extensions (journalctl -u mopidy -b)"
    fi

    # Checked only when configured, so a plain-HAT install is not nagged about
    # hardware it does not have. Read through sysfs rather than `hwclock -r`,
    # which needs root -- and --verify is meant to run without sudo.
    if [ -n "$CONFIG_TXT" ] && grep -qE '^dtoverlay=i2c-rtc,ds3231' "$CONFIG_TXT" 2>/dev/null; then
        if [ -r "$RTC_SYSFS/time" ]; then
            local rtc_utc rtc_local
            rtc_utc="$(cat "$RTC_SYSFS/date") $(cat "$RTC_SYSFS/time")"
            # sysfs always reports UTC, which reads as wrong by an hour or two
            # against a wall clock, so show the same instant in local time
            # too. Both dates are given because near midnight they differ.
            # Empty if date cannot parse it, and the line then shows UTC alone.
            rtc_local=$(date -d "$rtc_utc UTC" '+%Y-%m-%d %H:%M:%S %Z' 2>/dev/null || true)
            ok "RTC readable ($rtc_utc UTC${rtc_local:+ = $rtc_local})"
        elif reboot_pending; then
            # Not a failure: the overlay is in config.txt and overlays only
            # apply at boot. Saying FAIL here sent someone hunting a bug that
            # was a pending reboot.
            warn "RTC overlay is configured but not loaded yet — reboot"
        else
            bad "the ds3231 overlay is configured but $RTC_SYSFS is not readable"
        fi
    fi

    if command -v pinctrl >/dev/null; then
        if pinctrl get 18 2>/dev/null | grep -q 'a0'; then
            ok "GPIO 18 is in ALT0 for I2S"
        else
            bad "GPIO 18 is not I2S BCLK — check 'pwr_pin =' and the overlay"
        fi
    fi

    if aplay -l 2>/dev/null | grep -q sndrpihifiberry; then
        ok "the DAC is present"
    else
        bad "no hifiberry card — check the overlay and reboot"
    fi

    if curl -sf -m 5 http://localhost:6680/epaper/status >/dev/null 2>&1; then
        ok "the panel's input API is answering"
    else
        bad "/epaper/status is not answering"
    fi

    printf '\n'
    if [ "$PROBLEMS" = 0 ]; then
        printf '%sEverything checks out.%s\n' "$BOLD" "$RESET"
    else
        printf '%s%d problem(s) above.%s\n' "$BOLD" "$PROBLEMS" "$RESET"
        return 1
    fi
}

# ------------------------------------------------------------------- main ---

do_verify=0
for arg in "$@"; do
    case "$arg" in
        --verify) do_verify=1 ;;
        --rtc)    WANT_RTC=1 ;;
        *) die "unknown option: $arg (expected --verify and/or --rtc)" ;;
    esac
done

if [ "$do_verify" = 1 ]; then
    # In an if, so set -e does not pre-empt the exit code we want to return.
    if verify; then exit 0; else exit 1; fi
fi

if [ "$(id -u)" != 0 ]; then
    die "run with sudo (or pass --verify to check without changing anything)"
fi

install_packages
configure_boot
configure_rtc
add_groups
clone_repos
install_extension
write_mopidy_conf
write_units
seed_music
enable_services

step "Done"
info "Add your own music to $TARGET_HOME/music, then: mopidy local scan"
info "Wire the buttons to GPIO 5, 6, 13, 16 and 26, each to a shared ground."
info "The panel needs 5V as well as 3.3V — see the header diagram in README.md."
if reboot_pending; then
    printf '\n    %sReboot, then run: ./setup.sh --verify%s\n' "$BOLD" "$RESET"
else
    printf '\n    %sNow run: ./setup.sh --verify%s\n' "$BOLD" "$RESET"
fi
