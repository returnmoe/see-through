#!/usr/bin/env bash
set -Eeuo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
image="${CONTRACT_IMAGE:-see-through:contract-test}"
prefix="see-through-contract-${RANDOM}-$$"
tmp="$(mktemp -d)"
containers=()

cleanup() {
    if ((${#containers[@]})); then
        docker rm -f "${containers[@]}" >/dev/null 2>&1 || true
    fi
    rm -rf "$tmp"
}
trap cleanup EXIT

if [[ "${SKIP_CONTRACT_BUILD:-0}" != 1 ]]; then
    docker build --target contract-test --tag "$image" "$root"
fi

ssh-keygen -q -t ed25519 -N '' -f "$tmp/key-one"
ssh-keygen -q -t ed25519 -N '' -f "$tmp/key-two"
ssh-keygen -q -t ed25519 -N '' -f "$tmp/key-wrong"
key_one="$(<"$tmp/key-one.pub")"
key_two="$(<"$tmp/key-two.pub")"

start() {
    local name="$1"
    shift
    containers+=("$name")
    docker run --detach --name "$name" "$@" "$image" >/dev/null
}

wait_healthy() {
    local name="$1"
    for _ in {1..90}; do
        state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$name")"
        [[ "$state" == healthy ]] && return 0
        [[ "$state" == exited || "$state" == dead ]] && { docker logs "$name"; return 1; }
        sleep 1
    done
    docker logs "$name"
    return 1
}

socket_state() {
    local name="$1" port="$2" expected="$3"
    docker exec "$name" python -c "import socket,sys; s=socket.socket(); s.settimeout(.5); rc=s.connect_ex(('127.0.0.1',$port)); sys.exit(0 if (rc==0)==($expected) else 1)"
}

capture_logs() {
    local name="$1" destination="$2"
    docker logs "$name" >"$destination" 2>&1
}

no_key="$prefix-no-key"
start "$no_key" --env SEE_THROUGH_PREFETCH=off
wait_healthy "$no_key"
socket_state "$no_key" 4321 True
socket_state "$no_key" 22 False
docker exec "$no_key" python -c 'import socket,sys; host=socket.gethostbyname(socket.gethostname()); s=socket.socket(); s.settimeout(.5); sys.exit(0 if s.connect_ex((host,4321)) != 0 else 1)'
docker exec "$no_key" python -c 'from pathlib import Path; matches=[]; [(matches.append(path) if b"/opt/venv/bin/python\x00-m\x00seethrough_server" in (path/"cmdline").read_bytes() else None) for path in Path("/proc").iterdir() if path.name.isdigit()]; assert len(matches)==1; status=(matches[0]/"status").read_text(); fields=dict(line.split(":",1) for line in status.splitlines() if ":" in line); assert fields["NoNewPrivs"].strip()=="1"; assert int(fields["CapBnd"].strip(),16)==0'
docker exec "$no_key" curl --fail --silent --show-error --max-time 3 \
    --header 'Host: 127.0.0.1:4321' http://127.0.0.1:4321/healthz >"$tmp/healthz.json"
grep -Fq '"status":"ok"' "$tmp/healthz.json"
capture_logs "$no_key" "$tmp/no-key.log"
grep -Fq 'SSH disabled:' "$tmp/no-key.log"

invalid="$prefix-invalid"
start "$invalid" --env SEE_THROUGH_PREFETCH=off --env 'PUBLIC_KEY=ssh-ed25519 invalid secret-marker'
wait_healthy "$invalid"
socket_state "$invalid" 22 False
capture_logs "$invalid" "$tmp/invalid.log"
! grep -Fq 'secret-marker' "$tmp/invalid.log"

valid="$prefix-valid"
containers+=("$valid")
docker run --detach --name "$valid" --publish 127.0.0.1::22 \
    --env SEE_THROUGH_PREFETCH=off --env "PUBLIC_KEY=$key_one" --env "SSH_PUBLIC_KEY=$key_two" \
    "$image" >/dev/null
wait_healthy "$valid"
socket_state "$valid" 22 True
[[ "$(docker exec "$valid" stat -c %a /run/see-through/authorized_keys)" == 600 ]]
[[ "$(docker exec "$valid" sh -c 'wc -l </run/see-through/authorized_keys')" == 2 ]]
docker exec "$valid" /usr/sbin/sshd -T -f /run/see-through/sshd_config >"$tmp/sshd-effective"
grep -Fxq 'passwordauthentication no' "$tmp/sshd-effective"
grep -Fxq 'kbdinteractiveauthentication no' "$tmp/sshd-effective"
grep -Eq '^permitrootlogin (without-password|prohibit-password)$' "$tmp/sshd-effective"
grep -Fxq 'allowtcpforwarding local' "$tmp/sshd-effective"
grep -Fxq 'permitopen 127.0.0.1:4321' "$tmp/sshd-effective"

mapped="$(docker port "$valid" 22/tcp | head -n1)"
host_port="${mapped##*:}"
for _ in {1..30}; do
    ssh-keyscan -p "$host_port" 127.0.0.1 >"$tmp/known-hosts" 2>/dev/null && [[ -s "$tmp/known-hosts" ]] && break
    sleep 1
done
fingerprint="$(ssh-keygen -l -E sha256 -f "$tmp/known-hosts" | awk 'NR==1 {print $2}')"
capture_logs "$valid" "$tmp/valid.log"
grep -Fq "$fingerprint" "$tmp/valid.log"
ssh -p "$host_port" -i "$tmp/key-one" -o BatchMode=yes -o StrictHostKeyChecking=yes \
    -o UserKnownHostsFile="$tmp/known-hosts" root@127.0.0.1 'see-through help >/dev/null'
ssh -p "$host_port" -i "$tmp/key-two" -o BatchMode=yes -o StrictHostKeyChecking=yes \
    -o UserKnownHostsFile="$tmp/known-hosts" root@127.0.0.1 true
! ssh -p "$host_port" -i "$tmp/key-wrong" -o BatchMode=yes -o StrictHostKeyChecking=yes \
    -o UserKnownHostsFile="$tmp/known-hosts" root@127.0.0.1 true

custom="$prefix-custom"
start "$custom" --env SEE_THROUGH_PREFETCH=off --env "SSH_PUBLIC_KEY=$key_one" \
    --env SEE_THROUGH_BIND_HOST=0.0.0.0 --env SEE_THROUGH_PORT=4545
wait_healthy "$custom"
socket_state "$custom" 4545 True
docker exec "$custom" /usr/sbin/sshd -T -f /run/see-through/sshd_config >"$tmp/custom-sshd-effective"
grep -Fxq 'permitopen 127.0.0.1:4545' "$tmp/custom-sshd-effective"

preexisting="$prefix-preexisting"
containers+=("$preexisting")
docker run --detach --name "$preexisting" --entrypoint /bin/bash \
    --env SEE_THROUGH_PREFETCH=off --env "PUBLIC_KEY=$key_one" "$image" -c \
    'mkdir -p /run/see-through; ssh-keygen -q -t ed25519 -N "" -f /run/see-through/ssh_host_ed25519_key; ssh-keygen -l -E sha256 -f /run/see-through/ssh_host_ed25519_key.pub | awk "{print \$2}" >/tmp/old-fingerprint; exec /usr/bin/tini -g -- /usr/local/bin/see-through-entrypoint' >/dev/null
wait_healthy "$preexisting"
old_fingerprint="$(docker exec "$preexisting" cat /tmp/old-fingerprint)"
new_fingerprint="$(docker exec "$preexisting" ssh-keygen -l -E sha256 -f /run/see-through/ssh_host_ed25519_key.pub | awk '{print $2}')"
[[ "$old_fingerprint" != "$new_fingerprint" ]]

exposed="$(docker image inspect "$image" --format '{{json .Config.ExposedPorts}}')"
[[ "$exposed" == '{"22/tcp":{}}' ]]
[[ "$(docker image inspect "$image" --format '{{.Architecture}}/{{.Os}}')" == amd64/linux ]]

echo "container security contract: OK"
