#!/usr/bin/env bash
set -Eeuo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

ssh-keygen -q -t ed25519 -N '' -f "$tmp/key-one"
ssh-keygen -q -t ed25519 -N '' -f "$tmp/key-two"

set +e
env -u PUBLIC_KEY -u SSH_PUBLIC_KEY \
    "$root/docker/validate_authorized_keys.py" "$tmp/none"
status=$?
set -e
[[ "$status" == 3 && ! -e "$tmp/none" ]]

PUBLIC_KEY="$(<"$tmp/key-one.pub")" \
    "$root/docker/validate_authorized_keys.py" "$tmp/public"
[[ "$(wc -l <"$tmp/public")" == 1 ]]
[[ "$(stat -c %a "$tmp/public")" == 600 ]]

SSH_PUBLIC_KEY="$(<"$tmp/key-two.pub")" \
    "$root/docker/validate_authorized_keys.py" "$tmp/ssh-public"
[[ "$(wc -l <"$tmp/ssh-public")" == 1 ]]

cp "$tmp/key-two.pub" "$tmp/source"
env -u PUBLIC_KEY -u SSH_PUBLIC_KEY \
    "$root/docker/validate_authorized_keys.py" "$tmp/from-source" "$tmp/source"
[[ "$(wc -l <"$tmp/from-source")" == 1 ]]
cmp "$tmp/key-two.pub" "$tmp/from-source"

PUBLIC_KEY="$(<"$tmp/key-one.pub")" \
SSH_PUBLIC_KEY="$(<"$tmp/key-two.pub")" \
    "$root/docker/validate_authorized_keys.py" "$tmp/override" "$tmp/source"
[[ "$(wc -l <"$tmp/override")" == 1 ]]
cmp "$tmp/key-two.pub" "$tmp/override"

PUBLIC_KEY="$(<"$tmp/key-one.pub")" \
    env -u SSH_PUBLIC_KEY "$root/docker/validate_authorized_keys.py" \
    "$tmp/source-over-public" "$tmp/source"
cmp "$tmp/key-two.pub" "$tmp/source-over-public"

set +e
PUBLIC_KEY="$(<"$tmp/key-one.pub")" SSH_PUBLIC_KEY='ssh-ed25519 not-base64 secret-marker' \
    "$root/docker/validate_authorized_keys.py" "$tmp/invalid" 2>"$tmp/error"
status=$?
set -e
[[ "$status" == 2 && ! -e "$tmp/invalid" ]]
! grep -q 'secret-marker' "$tmp/error"

printf 'not-a-key\n' >"$tmp/invalid-source"
set +e
env -u PUBLIC_KEY -u SSH_PUBLIC_KEY \
    "$root/docker/validate_authorized_keys.py" "$tmp/invalid-source-output" \
    "$tmp/invalid-source" 2>"$tmp/source-error"
status=$?
set -e
[[ "$status" == 2 && ! -e "$tmp/invalid-source-output" ]]
! grep -q 'not-a-key' "$tmp/source-error"

[[ "$(env -u SEE_THROUGH_BIND_HOST -u SEE_THROUGH_PORT "$root/docker/listener_contract.py")" == "127.0.0.1:4321" ]]
[[ "$(SEE_THROUGH_BIND_HOST=0.0.0.0 SEE_THROUGH_PORT=4545 "$root/docker/listener_contract.py")" == "127.0.0.1:4545" ]]
[[ "$(SEE_THROUGH_BIND_HOST=:: SEE_THROUGH_PORT=4545 "$root/docker/listener_contract.py")" == "[::1]:4545" ]]
[[ "$(SEE_THROUGH_BIND_HOST=10.0.0.5 SEE_THROUGH_PORT=5000 "$root/docker/listener_contract.py")" == "10.0.0.5:5000" ]]
! SEE_THROUGH_BIND_HOST=localhost "$root/docker/listener_contract.py" >/dev/null 2>&1
! SEE_THROUGH_PORT=22 "$root/docker/listener_contract.py" >/dev/null 2>&1

python3 "$root/scripts/container/test-healthcheck.py"

echo "entrypoint helper contract: OK"
