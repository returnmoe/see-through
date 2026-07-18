# Container release process

Container images are built only from `development`. Production and semantic
release workflows promote an existing manifest; they never rebuild it.

The deployable image is an OCI container package in GitHub Container Registry
(GHCR), not a downloadable GitHub Actions artifact. The Development image
workflow uploads an Actions artifact named `image-policy-<commit>` for audit
reports only. RunPod pulls the container from
`ghcr.io/returnmoe/see-through`.

## Branches and tags

| Source | GHCR tags |
| --- | --- |
| `development` push | `dev-sha-<commit>`, `tree-<git-tree>`, `development` |
| Approved RunPod validation | `validated-tree-<git-tree>` |
| `master` push | `sha-<commit>`, `edge` |
| `vX.Y.Z` on promoted master | `X.Y.Z`, `X.Y`, `X`, `latest` |

Master promotion computes the commit's Git tree and requires `tree-*` and
`validated-tree-*` to resolve to the same plain `linux/amd64` manifest. A direct
master change or conflict resolution therefore cannot bypass development-image
validation.

## One-time repository configuration

GitHub registers a `workflow_run` trigger only when its workflow file exists on
the repository's default branch. Complete this bootstrap before the first
`development` push:

1. Commit and push `.github/workflows/` to the current default branch (`main`
   in a new checkout of this repository).
2. Rename `main` to `master`, push it, and make `master` the repository default.
   Confirm that `.github/workflows/development-image.yml` is visible on
   `master` before continuing.
3. Create `development` from that exact `master` commit and push it.
4. Protect both branches, prohibit direct pushes to `master`, and require CI on
   pull requests.
5. Create a protected GitHub Environment named `runpod-validation`.
6. Create a protected GitHub Environment named `production` and require release
   approval for master and semantic-version promotions.

The first `development` push runs CI and then the Development image workflow.
That workflow can upload the initial container package while it is private, but
its final publication job intentionally stops until anonymous pulls work:

1. Open the repository package `ghcr.io/returnmoe/see-through` in GitHub
   Packages and change its visibility to **Public**.
2. Return to the same Development image workflow run and choose
   **Re-run failed jobs**. Do not restart the complete workflow: the already
   built and scanned candidate is reused by the failed publication job.
3. Confirm that an anonymous client can inspect the reported digest and
   `dev-sha-<commit>` tag.

Normal `development` pushes require no manual dispatch. If CI succeeded for the
current `development` head but the downstream `workflow_run` event was missed
during initial repository setup, run **Development image** manually from the
repository's default branch. The manual path refuses non-default workflow refs
and refuses to build unless the exact current `development` commit has a
successful push CI run.

## Candidate acceptance

The development workflow must pass the dependency lock, contract image, source
security scans, SBOM vulnerability policy, offline import checks, and image
budget before advancing the moving tag. The enforced budgets are:

- 1.25 GiB maximum compressed layer;
- 6 GiB maximum compressed image;
- 12 GiB maximum logical root filesystem;
- Docker schema 2 with gzip layers;
- one `linux/amd64` deployment manifest;
- no model weights, Hugging Face cache, Node runtime, or reference UI sources.

Use the immutable candidate for the manual RunPod checklist in
[runpod.md](runpod.md). Copy the complete digest from the Development image
workflow summary and configure RunPod with:

```text
ghcr.io/returnmoe/see-through@sha256:<64-hex-digest>
```

The immutable `dev-sha-<40-character-commit>` tag is also suitable for a test
Pod, but the digest is the strongest identity. After testing, dispatch the
validation workflow with the development commit SHA from the `master` workflow
definition and approve the protected environment. Only then merge the identical
tree to `master`.

## Stable releases

Create a strict `vX.Y.Z` tag on a promoted master commit. The release workflow
checks that the immutable master tag already exists and resolves to the validated
digest. Moving major, minor, and `latest` aliases are recalculated from published
stable versions so an older tag cannot move them backward.

Use the image digest in long-lived RunPod templates. Moving aliases are intended
for discovery, not reproducibility.
