#!/bin/bash

set -eu

# ``systemd-run --scope`` re-executes this script inside the transient scope.
# The child writes an atomic acknowledgement before replacing itself with the
# requested command; the parent uses that token to distinguish its own scope
# from a colliding or partially created unit with the same name.
if [[ ${1-} == --scope-child ]]; then
    if (($# < 5)) || [[ $4 != -- ]]; then
        printf 'run_bounded: invalid internal scope-child invocation\n' >&2
        exit 125
    fi
    acknowledgement=$2
    acknowledgement_token=$3
    shift 4
    acknowledgement_temporary="${acknowledgement}.${BASHPID}.tmp"
    printf '%s\n' "$acknowledgement_token" >"$acknowledgement_temporary"
    mv -f -- "$acknowledgement_temporary" "$acknowledgement"
    exec "$@"
fi

# Randomized names avoid ordinary collisions.  An explicit --unit remains
# supported for tests, but launch still fails if systemd already knows it.
unit="trafficlab-test-guard-${BASHPID}-${RANDOM}-${RANDOM}"
memory_high=""
memory_max=""
swap_max=""
wall_time=""
kill_after=""
scope_owned=0
launcher_pid=""
cleanup_running=0
ownership_directory=""
ownership_acknowledgement=""
ownership_token=""

fail() {
    printf 'run_bounded: %s (unit %s.scope)\n' "$1" "$unit" >&2
    exit 125
}

require_value() {
    option=$1
    count=$2
    if ((count < 2)); then
        fail "required option ${option} needs a value"
    fi
    if [[ -z $3 ]]; then
        fail "required option ${option} needs a nonempty value"
    fi
}

while (($#)); do
    case $1 in
        --memory-high|--memory-max|--swap-max|--wall-time|--kill-after|--unit)
            require_value "$1" "$#" "${2-}"
            option=$1
            value=$2
            shift 2
            case $option in
                --memory-high) memory_high=$value ;;
                --memory-max) memory_max=$value ;;
                --swap-max) swap_max=$value ;;
                --wall-time) wall_time=$value ;;
                --kill-after) kill_after=$value ;;
                --unit) unit=$value ;;
            esac
            ;;
        --)
            shift
            break
            ;;
        *) fail "unknown option $1; expected a named limit option or --" ;;
    esac
done

[[ -n $memory_high ]] || fail "required option --memory-high is missing"
[[ -n $memory_max ]] || fail "required option --memory-max is missing"
[[ -n $swap_max ]] || fail "required option --swap-max is missing"
[[ -n $wall_time ]] || fail "required option --wall-time is missing"
[[ -n $kill_after ]] || fail "required option --kill-after is missing"
[[ $unit =~ ^[A-Za-z0-9][A-Za-z0-9_-]*$ ]] || fail "unit name must contain only letters, digits, underscores, or hyphens"
(($#)) || fail "missing command after --"
command=("$@")
environment_count=0
while ((environment_count < ${#command[@]})) && \
    [[ ${command[$environment_count]} =~ ^[A-Za-z_][A-Za-z0-9_]*=.*$ ]]; do
    environment_count=$((environment_count + 1))
done
if ((environment_count == ${#command[@]})); then
    fail "missing command after leading environment assignments"
fi
if ((environment_count > 0)); then
    command=(env "${command[@]}")
fi

normalize_memory() {
    input=$1
    label=$2
    if [[ ! $input =~ ^([0-9]+)([KMG]?)$ ]]; then
        fail "${label} memory value must be a nonnegative integer with an optional K, M, or G suffix"
    fi
    digits=${BASH_REMATCH[1]}
    suffix=${BASH_REMATCH[2]}
    digits=${digits##+(0)}
    [[ -n $digits ]] || digits=0
    case $suffix in
        '') multiplier=1 ;;
        K) multiplier=1024 ;;
        M) multiplier=1048576 ;;
        G) multiplier=1073741824 ;;
    esac
    maximum=$((9223372036854775807 / multiplier))
    maximum_text=$maximum
    if ((${#digits} > ${#maximum_text})) || \
        { ((${#digits} == ${#maximum_text})) && [[ $digits > $maximum_text ]]; }; then
        fail "${label} memory value is outside the supported integer range"
    fi
    NORMALIZED_MEMORY=$((10#$digits * multiplier))
}

validate_duration() {
    input=$1
    label=$2
    if [[ ! $input =~ ^([0-9]+)(ms|s|m)$ ]]; then
        fail "${label} duration must be a positive integer followed by ms, s, or m"
    fi
    digits=${BASH_REMATCH[1]}
    duration_suffix=${BASH_REMATCH[2]}
    digits=${digits##+(0)}
    [[ -n $digits ]] || fail "${label} duration must be positive"
    if [[ $duration_suffix == ms ]]; then
        while ((${#digits} < 4)); do
            digits="0${digits}"
        done
        NORMALIZED_DURATION="${digits:0:${#digits}-3}.${digits: -3}s"
    else
        NORMALIZED_DURATION="${digits}${duration_suffix}"
    fi
}

shopt -s extglob
normalize_memory "$memory_high" "--memory-high"
memory_high_bytes=$NORMALIZED_MEMORY
normalize_memory "$memory_max" "--memory-max"
memory_max_bytes=$NORMALIZED_MEMORY
normalize_memory "$swap_max" "--swap-max"
validate_duration "$wall_time" "--wall-time"
wall_time=$NORMALIZED_DURATION
validate_duration "$kill_after" "--kill-after"
kill_after=$NORMALIZED_DURATION
((memory_high_bytes < memory_max_bytes)) || fail "--memory-high must be less than --memory-max"

for required_command in env mktemp mv rm setsid systemd-run systemctl timeout; do
    command -v "$required_command" >/dev/null 2>&1 || fail "required command ${required_command} is unavailable"
done
if ! timeout --kill-after=1s 10s systemctl --user is-system-running >/dev/null 2>&1; then
    fail "systemd user manager is unavailable or not running"
fi

scope_state() {
    # Treat an unrecognized systemd response as an inspection failure, not as
    # an inactive scope.  Cleanup must fail closed when descendant ownership
    # or liveness cannot be established reliably.
    state_output=""
    state_status=0
    state_output=$(timeout --kill-after=1s 10s systemctl --user is-active "${unit}.scope" 2>&1) || state_status=$?
    state=${state_output%%$'\n'*}
    case $state in
        active|activating|reloading|deactivating) return 0 ;;
        inactive|failed|unknown) return 1 ;;
        *)
            printf 'run_bounded: cannot verify scope state: %s (unit %s.scope)\n' \
                "${state_output:-systemctl status $state_status}" "$unit" >&2
            return 2
            ;;
    esac
}

stop_launcher() {
    # The launcher owns a new process group via setsid.  Signal both its PID and
    # group so a systemd-run helper cannot outlive a failed scope launch.
    [[ -n $launcher_pid ]] || return 0
    kill -TERM "$launcher_pid" 2>/dev/null || true
    kill -TERM -- "-$launcher_pid" 2>/dev/null || true
    for ((attempt = 0; attempt < 20; attempt++)); do
        if ! kill -0 "$launcher_pid" 2>/dev/null && ! kill -0 -- "-$launcher_pid" 2>/dev/null; then
            wait "$launcher_pid" 2>/dev/null || true
            launcher_pid=""
            return 0
        fi
        sleep 0.01
    done
    kill -KILL "$launcher_pid" 2>/dev/null || true
    kill -KILL -- "-$launcher_pid" 2>/dev/null || true
    wait "$launcher_pid" 2>/dev/null || true
    launcher_pid=""
}

current_scope_is_owned() {
    description=""
    description_status=0
    description=$(timeout --kill-after=1s 10s systemctl --user show \
        "${unit}.scope" -p Description --value 2>/dev/null) || description_status=$?
    ((description_status == 0)) || return 2
    if [[ $description == "$ownership_token" ]]; then
        return 0
    fi
    return 1
}

prove_ownership() {
    # Prefer the acknowledgement written from inside the scope; Description is
    # a fallback for the short interval before that file becomes observable.
    if [[ -f $ownership_acknowledgement ]] && \
        [[ $(<"$ownership_acknowledgement") == "$ownership_token" ]]; then
        scope_owned=1
        return 0
    fi
    if current_scope_is_owned; then
        scope_owned=1
        return 0
    fi
    return 1
}

cleanup_scope() {
    # Never signal a scope until its per-launch token proves ownership.  This
    # is the central safety invariant: a name collision may fail the command,
    # but it must not let this wrapper kill an unrelated user's processes.
    CLEANUP_FOUND_ACTIVE=0
    ((cleanup_running == 0)) || return 0
    cleanup_running=1
    if ((scope_owned == 0)); then
        stop_launcher
        if [[ ! -f $ownership_acknowledgement ]] || \
            [[ $(<"$ownership_acknowledgement") != "$ownership_token" ]]; then
            cleanup_running=0
            return 0
        fi
        ownership_status=0
        current_scope_is_owned || ownership_status=$?
        if ((ownership_status == 2)); then
            cleanup_running=0
            return 1
        fi
        if ((ownership_status == 1)); then
            cleanup_running=0
            return 0
        fi
        scope_owned=1
    fi
    ownership_status=0
    current_scope_is_owned || ownership_status=$?
    if ((ownership_status == 2)); then
        stop_launcher
        cleanup_running=0
        return 1
    fi
    if ((ownership_status == 1)); then
        stop_launcher
        scope_owned=0
        cleanup_running=0
        return 0
    fi
    state_status=0
    scope_state || state_status=$?
    if ((state_status == 2)); then
        stop_launcher
        cleanup_running=0
        return 1
    fi
    if ((state_status == 0)); then
        CLEANUP_FOUND_ACTIVE=1
        ownership_status=0
        current_scope_is_owned || ownership_status=$?
        if ((ownership_status == 2)); then
            stop_launcher
            cleanup_running=0
            return 1
        fi
        if ((ownership_status == 1)); then
            stop_launcher
            CLEANUP_FOUND_ACTIVE=0
            scope_owned=0
            cleanup_running=0
            return 0
        fi
        if ! timeout --kill-after=1s 10s systemctl --user kill \
            --kill-whom=all --signal=KILL "${unit}.scope"; then
            state_status=0
            scope_state || state_status=$?
            if ((state_status == 1)); then
                CLEANUP_FOUND_ACTIVE=0
                stop_launcher
                cleanup_running=0
                return 0
            fi
            printf 'run_bounded: failed to kill complete scope (unit %s.scope)\n' "$unit" >&2
            stop_launcher
            cleanup_running=0
            return 1
        fi
        for ((attempt = 0; attempt < 100; attempt++)); do
            state_status=0
            scope_state || state_status=$?
            if ((state_status == 1)); then
                stop_launcher
                cleanup_running=0
                return 0
            fi
            if ((state_status == 2)); then
                stop_launcher
                cleanup_running=0
                return 1
            fi
            sleep 0.05
        done
        printf 'run_bounded: scope did not become inactive within five seconds (unit %s.scope)\n' "$unit" >&2
        stop_launcher
        cleanup_running=0
        return 1
    fi
    stop_launcher
    cleanup_running=0
    return 0
}

on_exit() {
    # Cleanup failure overrides the child result because an unverifiable live
    # scope breaks the wrapper's resource-bound guarantee.  Likewise, a child
    # that exits zero while leaving descendants behind is not a successful run.
    original_status=$1
    trap - EXIT INT TERM HUP
    if ! cleanup_scope; then
        [[ -z $ownership_directory ]] || rm -rf -- "$ownership_directory"
        exit 125
    fi
    [[ -z $ownership_directory ]] || rm -rf -- "$ownership_directory"
    if ((original_status == 0 && CLEANUP_FOUND_ACTIVE)); then
        printf 'run_bounded: successful command left descendants active; complete scope was killed (unit %s.scope)\n' \
            "$unit" >&2
        exit 125
    fi
    exit "$original_status"
}

trap 'on_exit $?' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

load_state=$(timeout --kill-after=1s 10s systemctl --user show "${unit}.scope" -p LoadState --value 2>&1) || \
    fail "cannot inspect requested unit before launch: ${load_state}"
if [[ $load_state != not-found ]]; then
    fail "requested unit already exists and is not owned by this guard"
fi

ownership_directory=$(mktemp -d "${TMPDIR:-/tmp}/trafficlab-test-guard.XXXXXXXX") || \
    fail "could not create private ownership directory"
chmod 700 "$ownership_directory" || fail "could not protect private ownership directory"
ownership_acknowledgement="${ownership_directory}/ack"
ownership_token="trafficlab-test-guard-token-${BASHPID}-${RANDOM}-${RANDOM}"

# Start systemd-run in a separate process group so pre-scope launch failures can
# be terminated locally.  Once ownership is acknowledged, cleanup operates on
# the complete systemd scope and therefore includes every descendant process.
setsid systemd-run --user --quiet --scope --collect --unit="$unit" \
    -p "Description=$ownership_token" \
    -p "MemoryHigh=$memory_high" \
    -p "MemoryMax=$memory_max" \
    -p "MemorySwapMax=$swap_max" \
    -p OOMPolicy=kill \
    "$0" --scope-child "$ownership_acknowledgement" "$ownership_token" -- \
    timeout --kill-after="$kill_after" "$wall_time" "${command[@]}" &
launcher_pid=$!

for ((attempt = 0; attempt < 100; attempt++)); do
    if prove_ownership; then
        break
    fi
    if ! kill -0 "$launcher_pid" 2>/dev/null; then
        break
    fi
    sleep 0.01
done

command_status=0
wait "$launcher_pid" || command_status=$?
launcher_pid=""
prove_ownership || true
if ((scope_owned == 0 && command_status != 0)); then
    fail "systemd-run failed to create and activate the guarded scope (status ${command_status})"
fi
exit "$command_status"
