#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -lt 4 ]]; then
    echo "usage: promote-image.sh REPOSITORY DIGEST immutable|moving TAG..." >&2
    exit 64
fi
repository="$1"
digest="$2"
mode="$3"
shift 3
[[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]]
[[ "$mode" == immutable || "$mode" == moving ]]

resolve() {
    docker buildx imagetools inspect "$1" 2>/dev/null | sed -n 's/^Digest:[[:space:]]*//p' | head -n1
}

for tag in "$@"; do
    target="$repository:$tag"
    existing="$(resolve "$target" || true)"
    if [[ -n "$existing" && "$mode" == immutable && "$existing" != "$digest" ]]; then
        echo "refusing to move immutable tag $target from $existing to $digest" >&2
        exit 1
    fi
    if [[ "$existing" == "$digest" ]]; then
        echo "$target already resolves to $digest"
        continue
    fi
    # The default wraps a single manifest in an index. RunPod receives a plain
    # Docker schema-2 manifest, so preserve the source object byte-for-byte.
    docker buildx imagetools create --prefer-index=false --tag "$target" "$repository@$digest"
    actual="$(resolve "$target")"
    [[ "$actual" == "$digest" ]] || { echo "promotion verification failed for $target" >&2; exit 1; }
done
