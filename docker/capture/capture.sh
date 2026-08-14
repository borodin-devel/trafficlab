#!/bin/sh
set -eu

interface_path=/sys/class/net/eth0/address
metadata_path=/trafficlab/capture.json
metadata_temporary_path=/trafficlab/capture.json.tmp
capture_path=/trafficlab/reference.pcapng.tmp
ordered_path=/trafficlab/reference.pcapng.ordered

rm -f "$metadata_temporary_path" "$metadata_path" "$capture_path" "$ordered_path"

if [ ! -r "$interface_path" ]; then
    echo "trafficlab capture: eth0 MAC is unavailable" >&2
    exit 1
fi

target_mac=$(tr '[:upper:]' '[:lower:]' < "$interface_path")
case "$target_mac" in
    [0-9a-f][0-9a-f]:[0-9a-f][0-9a-f]:[0-9a-f][0-9a-f]:[0-9a-f][0-9a-f]:[0-9a-f][0-9a-f]:[0-9a-f][0-9a-f]) ;;
    *)
        echo "trafficlab capture: eth0 MAC is malformed" >&2
        exit 1
        ;;
esac

if [ "$target_mac" = "00:00:00:00:00:00" ]; then
    echo "trafficlab capture: eth0 MAC is zero" >&2
    exit 1
fi

first_octet=${target_mac%%:*}
second_nibble=${first_octet#?}
case "$second_nibble" in
    1 | 3 | 5 | 7 | 9 | b | d | f)
        echo "trafficlab capture: eth0 MAC is multicast" >&2
        exit 1
        ;;
esac

printf '{"interface":"eth0","target_mac":"%s"}\n' "$target_mac" > "$metadata_temporary_path"
mv -f "$metadata_temporary_path" "$metadata_path"

finalize_capture() {
    reordercap "$capture_path" "$ordered_path"
    mv -f "$ordered_path" "$capture_path"
}

interrupt_capture() {
    trap - INT TERM
    kill -INT "$capture_pid" 2>/dev/null || true
    if wait "$capture_pid"; then
        finalize_capture
        exit 0
    else
        status=$?
        rm -f "$ordered_path"
        exit "$status"
    fi
}

trap interrupt_capture INT TERM
dumpcap -i eth0 -p -q -w - > "$capture_path" &
capture_pid=$!
wait "$capture_pid"
trap - INT TERM
finalize_capture
