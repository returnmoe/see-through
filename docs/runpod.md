# Running See-through on RunPod

The See-through image is designed to be private-by-default:

- the only advertised container port is SSH on port 22;
- SSH starts only when `PUBLIC_KEY` or `SSH_PUBLIC_KEY` contains at least one
  valid OpenSSH public key;
- password login is disabled and root can authenticate only with a public key;
- the web application listens on `127.0.0.1:4321` unless you explicitly
  override it;
- model weights are downloaded after startup and are never part of the image.

The container is published as a GHCR package, not as a downloadable GitHub
Actions artifact. The workflow's downloadable artifact contains only the image
policy report. Copy the immutable container digest or `dev-sha-*` tag from the
Development image workflow summary, and replace Pod connection values in angle
brackets with the values shown by RunPod.

## 1. Generate an SSH key

Use a dedicated Ed25519 key if possible:

```bash
ssh-keygen -t ed25519 -a 64 -f ~/.ssh/see-through-runpod
```

Add the contents of `~/.ssh/see-through-runpod.pub` to your RunPod account's SSH
keys. RunPod normally supplies account keys to a Pod through `PUBLIC_KEY`; its
[environment-variable reference](https://docs.runpod.io/pods/templates/environment-variables)
documents that injection. You can instead put the public key in the template as
either `PUBLIC_KEY` or `SSH_PUBLIC_KEY`; RunPod documents `SSH_PUBLIC_KEY` as the
[per-Pod override](https://docs.runpod.io/pods/configuration/use-ssh#override-your-public-key-for-a-specific-pod).

Both variables may contain multiple newline-separated public keys. When both
are set, the image combines and deduplicates them. If any supplied line is not
a valid public key, SSH remains disabled; a partial key set is never accepted.
The image does not print the submitted key material.

## 2. Create the RunPod template

Use these settings:

| Setting | Value |
| --- | --- |
| Container image | `ghcr.io/returnmoe/see-through@sha256:<64-hex-digest>` (preferred) or `ghcr.io/returnmoe/see-through:dev-sha-<40-character-commit>` |
| GPU | One 24 GB NVIDIA GPU recommended; RTX 4090/3090, RTX A5000, or L4 are practical defaults |
| System RAM | 32 GB recommended; 48 GB for offload-heavy profiles |
| Container disk | 60 GB recommended |
| Volume disk | Optional |
| Volume mount | `/workspace` when a volume is used |
| Exposed TCP ports | `22` only |
| Exposed HTTP ports | None |
| Docker command | Leave blank |
| Public IP | Required for RunPod's direct TCP SSH connection |

See [hardware and resolution sizing](hardware.md) before selecting a GPU. It
documents the 8 GiB floor, Auto profile thresholds, VRAM estimates, resolution
tradeoffs, current RunPod GPU examples, system-RAM targets, and model-cache
sizes. Select exactly one GPU: the inference queue is serial and the application
does not combine memory or work across multiple GPUs. In particular, an
8-GPU B300 machine does not give this application pooled VRAM, and the current
CUDA 12.8 image has not been validated for B300. Use RunPod's
**Additional filters → CUDA Versions** control to select a host compatible with
the image's CUDA 12.8 runtime. The image supports NVIDIA GPUs only; do not select
an AMD GPU.

Prefer the complete digest shown in the workflow summary. A full semantic
version or immutable development SHA tag is also reproducible. Do not use
`edge`, `development`, or `latest` for a Pod you need to reproduce later.

The default model cache is on the Pod's local container disk:

```text
/home/seethrough/.cache/huggingface
```

This is normally faster than network storage and also works on ephemeral Pods
without `/workspace`. The full BF16 bundle is approximately 13.5 GB and the NF4
bundle approximately 5.7 GB, so allocate enough container disk for the image,
the selected bundle, temporary download data, and outputs.

The API starts first. It then downloads the bundle selected for the detected GPU
in the background. A failed or interrupted download does not stop SSH or the web
application; its status and retry control remain available in the UI.

### Optional persistence

Job metadata and results also default to local container storage. To persist
them on a mounted RunPod volume, set:

```text
SEE_THROUGH_DATA_DIR=/workspace/see-through
```

To persist Hugging Face downloads as well, set:

```text
SEE_THROUGH_MODEL_CACHE=/workspace/.cache/huggingface
```

Keeping model weights on `/workspace` avoids downloading them again after an
ephemeral container is replaced, but can make model loading slower when that
path is backed by network storage. It is valid to persist results while leaving
the model cache on the faster local container disk.

`SEE_THROUGH_PREFETCH` defaults to `auto`. It may be set to `bf16`, `nf4`, or
`off`. With `off`, use **Prepare now** in the UI's **Model preparation** card
before running a job.

## 3. Verify the SSH host key

Every container start generates a new Ed25519 SSH host key. Open the Pod's
**Logs → Container Logs** view (sometimes described as the web serial console)
and find the line containing the SHA-256 SSH host-key fingerprint.

Do not accept the first SSH connection until the `SHA256:...` value in the SSH
prompt exactly matches the value in the RunPod log. This protects the initial
connection from connecting to the wrong endpoint.

RunPod maps container port 22 to an external TCP port. Its
[full-SSH instructions](https://docs.runpod.io/pods/configuration/use-ssh#full-ssh-via-public-ip-with-key-authentication)
confirm that custom images need an SSH daemon, exposed TCP port 22, and a public
IP. Use the public IP and external port displayed in **Connect → Direct TCP
Ports**:

```bash
ssh -i ~/.ssh/see-through-runpod \
  -p <EXTERNAL_SSH_PORT> \
  root@<POD_PUBLIC_IP>
```

The server accepts no password, keyboard-interactive authentication, agent
forwarding, X11 forwarding, remote forwarding, or arbitrary local forwarding.
Root access grants complete control of the container, so protect the private key.

Host keys intentionally change after a container restart or replacement. If the
IP and mapped port are reused, first verify the new fingerprint in the RunPod
logs and then remove only that stale entry:

```bash
ssh-keygen -R "[<POD_PUBLIC_IP>]:<EXTERNAL_SSH_PORT>"
```

## 4. Open the web application through SSH

Create a local tunnel in a terminal and leave it running:

```bash
ssh -i ~/.ssh/see-through-runpod \
  -p <EXTERNAL_SSH_PORT> \
  -L 4321:127.0.0.1:4321 \
  root@<POD_PUBLIC_IP>
```

Then open <http://127.0.0.1:4321> in your local browser.

The web application is not sent through a RunPod HTTP proxy in this setup. It
remains bound to the container's loopback interface and is reachable only
through the authenticated SSH tunnel.

## 5. Run a decomposition

1. Wait for **Model preparation** to report that the selected bundle is ready.
   The UI shows not-downloaded, downloading, ready, and failed states. If a
   download fails, inspect the displayed error and use its Prepare/Retry action.
   You can continue inspecting the system and queue while a download runs.
2. Upload one BMP, JPEG, PNG, WebP, or JXL image.
3. Leave the memory profile on **Auto**, or select BF16, group offload, NF4, or
   block swap explicitly.
4. Select a LayerDiff preset or enter a multiple of 64 from 768 through 10240.
   The released-model default and validated baseline is 1280; larger settings
   are untiled and experimental.
5. Set the seed, LayerDiff steps, and Marigold working size if the defaults do
   not fit the run. The UI updates its per-GPU VRAM planning range immediately.
6. Submit the job and follow the phase and log drawer. The GPU queue is serial.
7. Inspect the reconstruction and semantic layers, then download the layered
   PSD, depth PSD, metadata, individual artifacts, or complete ZIP.

Queued jobs survive a service restart when their data directory survives. A job
that was actively using the GPU is marked `interrupted` rather than silently
restarted.

You can inspect the same state from the SSH shell:

```bash
curl --fail --silent http://127.0.0.1:4321/healthz
curl --fail --silent http://127.0.0.1:4321/api/system
curl --fail --silent http://127.0.0.1:4321/api/jobs
```

The application source is installed at `/opt/see-through`. Results are under
`SEE_THROUGH_DATA_DIR` (default `/home/seethrough/data`). Full job logs live
beside each job's metadata.

To copy a completed artifact without using the browser, use `scp` with RunPod's
mapped SSH port:

```bash
scp -i ~/.ssh/see-through-runpod \
  -P <EXTERNAL_SSH_PORT> \
  root@<POD_PUBLIC_IP>:/home/seethrough/data/jobs/<JOB_ID>/artifacts.zip .
```

## Explicitly exposing the web port

The listener address and port can be overridden:

```text
SEE_THROUGH_BIND_HOST=0.0.0.0
SEE_THROUGH_PORT=8080
```

You must also expose the selected port in the RunPod template. The image still
advertises only port 22, so this cannot happen accidentally through its Docker
metadata.

**This mode has no application authentication and no built-in TLS. Anyone who
can reach the published port can submit GPU jobs, view logs, and download or
delete job artifacts.** Use it only behind an access-controlled proxy or on a
trusted network. The SSH tunnel remains the recommended mode.

When the web port changes but the listener remains loopback-only, adjust the
tunnel accordingly:

```bash
ssh -i ~/.ssh/see-through-runpod \
  -p <EXTERNAL_SSH_PORT> \
  -L 8080:127.0.0.1:8080 \
  root@<POD_PUBLIC_IP>
```

## Troubleshooting

### Port 22 is not listening

Check Container Logs. Neither key environment variable may be present, a value
may be blank, or at least one supplied key line may be invalid. The web process
continues on loopback even when SSH fails closed.

### The browser cannot reach localhost

Confirm the SSH session with `-L` is still running, use the configured local
port, and check application health and system state from the SSH shell:

```bash
curl --fail http://127.0.0.1:4321/healthz
curl --fail http://127.0.0.1:4321/api/system
```

### Model preparation reports insufficient space

Increase the container disk, clear obsolete local caches, or attach a sufficiently
large volume and set `SEE_THROUGH_MODEL_CACHE`. The downloader reserves an extra
5 GiB and refuses to begin when that safety margin is unavailable.

### A model download was interrupted

Use the model-preparation status card's Prepare/Retry action. Downloads are
revision-pinned and resumable; completed inference switches to offline model
loading so a repository update cannot alter an in-progress job.

### The Pod starts but no GPU is listed

Check that the selected RunPod machine exposes an NVIDIA GPU and supports the
CUDA 12.8 runtime. Automatic model preparation stays idle below 8 GiB of
detected VRAM, and a submitted job fails with the explicit minimum-memory error.

## Validating a development image

Before promoting a development image, deploy the digest from its workflow
summary (preferred) or its immutable `dev-sha-<40-character-commit>` tag and
verify:

- the image pulls and starts successfully;
- only the configured TCP ports are reachable;
- `PUBLIC_KEY`, `SSH_PUBLIC_KEY`, and the combined case authenticate correctly;
- the logged fingerprint matches the live server and changes on a fresh start;
- default model downloads use local container storage;
- optional `/workspace` overrides use the requested paths;
- BF16 and NF4 each complete a representative job on suitable hardware;
- PSD, depth PSD, reconstruction, layers, logs, and ZIP download correctly;
- the explicit bind/port overrides behave as documented.

After this manual test, run the repository's RunPod validation workflow for the
same immutable digest. Production automation will promote only that exact image.
