#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
    echo "usage: validate-image.sh IMAGE_REF [REPORT_DIR]" >&2
    exit 64
fi
ref="$1"
report_dir="${2:-image-policy-report}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
mkdir -p "$report_dir"

docker buildx imagetools inspect "$ref" --raw >"$report_dir/index.json"
if digest="$(python3 "$root/scripts/container/image-policy.py" "$report_dir/index.json" --select-amd64 2>/dev/null)"; then
    repository="${ref%@*}"
    if [[ "$repository" == "$ref" && "${repository##*/}" == *:* ]]; then
        repository="${repository%:*}"
    fi
    deployment_ref="$repository@$digest"
    docker buildx imagetools inspect "$deployment_ref" --raw >"$report_dir/manifest.json"
else
    deployment_ref="$ref"
    digest="$(docker buildx imagetools inspect "$ref" | sed -n 's/^Digest:[[:space:]]*//p' | head -n1)"
    cp "$report_dir/index.json" "$report_dir/manifest.json"
fi
docker buildx imagetools inspect "$deployment_ref" --format '{{json .Image}}' >"$report_dir/config.json"
python3 "$root/scripts/container/image-policy.py" "$report_dir/manifest.json" \
    --image-config "$report_dir/config.json" --report "$report_dir/policy.json"

printf 'deployment_ref=%s\n' "$deployment_ref"
printf 'digest=%s\n' "$digest"
if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    printf 'deployment_ref=%s\ndigest=%s\n' "$deployment_ref" "$digest" >>"$GITHUB_OUTPUT"
fi
