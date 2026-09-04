#!/usr/bin/env bash

set -u

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
mkdir -p runs

shopt -s nullglob
dump_directories=(dumps/*/)

if ((${#dump_directories[@]} == 0)); then
    printf 'No dump directories found under dumps/.\n' >&2
    exit 1
fi

pids=()

for dump_directory in "${dump_directories[@]}"; do
    (
        name=${dump_directory%/}
        name=${name##*/}
        experiment="runs/${name}.toml"
        SECONDS=0

        if cp examples/configs/maximum_quality.toml "$experiment" &&
            sed -i "s#directory = \"../../runs/maximum-quality\"#directory = \"./${name}\"#" "$experiment" &&
            uv run --locked trafficlab import-run "$experiment" "$dump_directory"; then
            status=0
        else
            status=$?
        fi

        printf '%s: %s seconds (status %s)\n' "$name" "$SECONDS" "$status"
        exit "$status"
    ) &
    pids+=("$!")
done

status=0
for pid in "${pids[@]}"; do
    wait "$pid" || status=1
done

exit "$status"
