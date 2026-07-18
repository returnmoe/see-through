#!/usr/bin/env bash
set -Eeuo pipefail

readonly RUNTIME_DIR=/run/see-through
readonly ROOT_SSH_DIR=/root/.ssh
readonly AUTHORIZED_KEYS_SOURCE="$ROOT_SSH_DIR/authorized_keys"
readonly AUTHORIZED_KEYS_RUNTIME="$RUNTIME_DIR/authorized_keys"
readonly SSHD_RUNTIME_CONFIG=/etc/ssh/sshd_config.d/99-see-through-runtime.conf
readonly SSHD_PID="$RUNTIME_DIR/sshd.pid"

web_pid=""
ssh_pid=""
stopping=0

stop_children() {
    local signal="${1:-TERM}"
    stopping=1
    if [[ -n "$ssh_pid" ]] && kill -0 "$ssh_pid" 2>/dev/null; then
        kill "-$signal" -- "-$ssh_pid" 2>/dev/null || kill "$signal" "$ssh_pid" 2>/dev/null || true
    fi
    if [[ -n "$web_pid" ]] && kill -0 "$web_pid" 2>/dev/null; then
        kill "-$signal" -- "-$web_pid" 2>/dev/null || kill "$signal" "$web_pid" 2>/dev/null || true
    fi
}

finish_signal() {
    stop_children TERM
    wait "$ssh_pid" 2>/dev/null || true
    wait "$web_pid" 2>/dev/null || true
    exit 143
}

trap finish_signal TERM INT HUP

export SEE_THROUGH_BIND_HOST="${SEE_THROUGH_BIND_HOST:-127.0.0.1}"
export SEE_THROUGH_PORT="${SEE_THROUGH_PORT:-4321}"
export SEE_THROUGH_DATA_DIR="${SEE_THROUGH_DATA_DIR:-/home/seethrough/data}"
export SEE_THROUGH_MODEL_CACHE="${SEE_THROUGH_MODEL_CACHE:-${HF_HOME:-/home/seethrough/.cache/huggingface}}"
export HF_HOME="$SEE_THROUGH_MODEL_CACHE"
export PYTHONUNBUFFERED=1

safe_storage_path() {
    local requested="$1"
    local resolved
    resolved="$(realpath -m -- "$requested")"
    case "$resolved/" in
        /home/seethrough/*|/workspace/*|/tmp/see-through/*)
            printf '%s\n' "$resolved"
            ;;
        *)
            printf 'Storage paths must be below /home/seethrough, /workspace, or /tmp/see-through: %s\n' "$requested" >&2
            return 64
            ;;
    esac
}

log_ssh_host_key_fingerprints() {
    local found=0
    local fingerprint
    local public_key
    for public_key in /etc/ssh/ssh_host_*_key.pub; do
        [[ -f "$public_key" ]] || continue
        fingerprint="$(ssh-keygen -l -E sha256 -f "$public_key")" || {
            printf 'Could not read SSH host-key fingerprint: %s\n' "$public_key" >&2
            return 1
        }
        printf 'SSH host key %s fingerprint: %s\n' "$(basename "$public_key")" "$fingerprint"
        found=1
    done
    if (( found == 0 )); then
        printf 'SSH host-key generation produced no public keys.\n' >&2
        return 1
    fi
}

SEE_THROUGH_DATA_DIR="$(safe_storage_path "$SEE_THROUGH_DATA_DIR")"
SEE_THROUGH_MODEL_CACHE="$(safe_storage_path "$SEE_THROUGH_MODEL_CACHE")"
HF_HOME="$SEE_THROUGH_MODEL_CACHE"
export SEE_THROUGH_DATA_DIR SEE_THROUGH_MODEL_CACHE HF_HOME
export SEE_THROUGH_SSH_REQUIRED=0

permit_open="$(/usr/local/libexec/see-through/listener_contract.py)"
install -d -m 0750 -o seethrough -g seethrough "$SEE_THROUGH_DATA_DIR" "$SEE_THROUGH_MODEL_CACHE"
install -d -m 0700 "$RUNTIME_DIR"
install -d -m 0700 "$ROOT_SSH_DIR"
install -d -m 0755 /run/sshd
if [[ -e /opt/see-through/workspace && ! -L /opt/see-through/workspace ]]; then
    printf '/opt/see-through/workspace exists and is not a symlink; refusing to replace it.\n' >&2
    exit 64
fi
ln -sfn "$SEE_THROUGH_DATA_DIR" /opt/see-through/workspace
rm -f "$AUTHORIZED_KEYS_RUNTIME" /etc/ssh/ssh_host_*_key /etc/ssh/ssh_host_*_key.pub \
    "$SSHD_RUNTIME_CONFIG" "$SSHD_PID"
/usr/local/libexec/see-through/write_runtime_env.py

server_module="${SEE_THROUGH_SERVER_MODULE:-seethrough_server}"
printf 'Starting See-through web service on %s:%s\n' "$SEE_THROUGH_BIND_HOST" "$SEE_THROUGH_PORT"
setsid setpriv --reuid=seethrough --regid=seethrough --init-groups --reset-env \
    --no-new-privs --bounding-set=-all \
    env HOME=/home/seethrough PATH=/opt/venv/bin:/usr/local/bin:/usr/bin:/bin \
    PYTHONPATH=/opt/see-through/common:/opt/see-through/server \
    PYTHONUNBUFFERED="$PYTHONUNBUFFERED" HF_HOME="$HF_HOME" \
    SEE_THROUGH_BIND_HOST="$SEE_THROUGH_BIND_HOST" SEE_THROUGH_PORT="$SEE_THROUGH_PORT" \
    SEE_THROUGH_DATA_DIR="$SEE_THROUGH_DATA_DIR" SEE_THROUGH_MODEL_CACHE="$SEE_THROUGH_MODEL_CACHE" \
    SEE_THROUGH_PREFETCH="${SEE_THROUGH_PREFETCH:-auto}" \
    /opt/venv/bin/python -m "$server_module" &
web_pid=$!

key_status=0
/usr/local/libexec/see-through/validate_authorized_keys.py \
    "$AUTHORIZED_KEYS_RUNTIME" "$AUTHORIZED_KEYS_SOURCE" || key_status=$?
case "$key_status" in
    0)
        cat >"$SSHD_RUNTIME_CONFIG" <<EOF
Port 22
AddressFamily any
PermitOpen $permit_open
PidFile $SSHD_PID
EOF
        ssh-keygen -A
        /usr/sbin/sshd -t
        export SEE_THROUGH_SSH_REQUIRED=1
        /usr/local/libexec/see-through/write_runtime_env.py
        printf 'SSH enabled: root public-key authentication only; permitted local-forward destination %s\n' "$permit_open"
        log_ssh_host_key_fingerprints
        setsid env -u PUBLIC_KEY -u SSH_PUBLIC_KEY /usr/sbin/sshd -D -e &
        ssh_pid=$!
        ;;
    2)
        printf 'SSH remains disabled because the supplied key set was rejected.\n' >&2
        ;;
    3)
        printf 'SSH disabled: no key found in SSH_PUBLIC_KEY, /root/.ssh/authorized_keys, or PUBLIC_KEY.\n'
        ;;
    *)
        printf 'SSH key validation failed unexpectedly (status %s); SSH remains disabled.\n' "$key_status" >&2
        ;;
esac

set +e
if [[ -n "$ssh_pid" ]]; then
    wait -n "$web_pid" "$ssh_pid"
    status=$?
else
    wait "$web_pid"
    status=$?
fi
set -e

if (( stopping == 0 )); then
    printf 'A supervised service exited with status %s; stopping the container.\n' "$status" >&2
fi
stop_children TERM
wait "$ssh_pid" 2>/dev/null || true
wait "$web_pid" 2>/dev/null || true
exit "$status"
