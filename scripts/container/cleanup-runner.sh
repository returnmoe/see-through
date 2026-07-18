#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${GITHUB_ACTIONS:-}" != true || "${RUNNER_OS:-}" != Linux ]]; then
    echo "cleanup-runner.sh is restricted to Linux GitHub Actions runners" >&2
    exit 64
fi
sudo rm -rf /usr/local/lib/android /usr/share/dotnet /opt/ghc /usr/local/.ghcup /opt/hostedtoolcache/CodeQL
sudo apt-get clean
docker builder prune --all --force || true
available_kib="$(df --output=avail / | tail -n1 | tr -d ' ')"
minimum_kib=$((25 * 1024 * 1024))
if (( available_kib < minimum_kib )); then
    echo "only $available_kib KiB free after cleanup; 25 GiB required" >&2
    df -h /
    exit 1
fi
df -h /
