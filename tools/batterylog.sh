#!/usr/bin/env bash
#
# Sample the PiSugar into a CSV once a minute, so a runtime test can be read
# back after the device has shut itself down at the low-battery threshold.
#
# PiSugar 3 reports charge and voltage but not current -- `get battery_i`
# answers a hard zero even on battery -- so runtime has to be measured by
# watching the charge fall rather than by reading power directly.
#
#   sudo systemctl enable --now batterylog
#
# Then charge to full, unplug, and leave it alone. `uptime_s` makes the
# shutdown point obvious when reading the log back.

set -u

LOG=${BATTERY_LOG:-$HOME/battery.csv}
SOCK=${PISUGAR_SOCK:-/tmp/pisugar-server.sock}
INTERVAL=${BATTERY_LOG_INTERVAL:-60}

ask() { echo "get $1" | timeout 2 nc -U "$SOCK" 2>/dev/null | awk '{print $2}'; }

playback() {
    curl -s --max-time 2 -H 'Content-Type: application/json' \
        -d '{"jsonrpc":"2.0","id":1,"method":"core.playback.get_state"}' \
        http://localhost:6680/mopidy/rpc 2>/dev/null \
        | jq -r '.result // "unknown"' 2>/dev/null
}

[ -s "$LOG" ] || echo "timestamp,uptime_s,percent,volts,plugged,playback" >> "$LOG"

while true; do
    # Appended a line at a time, so losing power costs at most one sample --
    # which matters here, since losing power is how the test ends.
    printf '%s,%s,%s,%s,%s,%s\n' \
        "$(date -Is)" \
        "$(cut -d' ' -f1 /proc/uptime | cut -d. -f1)" \
        "$(ask battery)" \
        "$(ask battery_v)" \
        "$(ask battery_power_plugged)" \
        "$(playback)" >> "$LOG"
    sleep "$INTERVAL"
done
