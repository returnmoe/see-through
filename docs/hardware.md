# Hardware and resolution sizing

## Short recommendation

For a first RunPod deployment, select **one NVIDIA GPU with 24 GB of VRAM**, at
least 32 GB of system RAM, and a 60 GB container disk. Leave the memory profile
on **Auto** and the resolution at **1280**. An RTX 4090 or RTX 3090 is usually the
most straightforward choice when available; an RTX A5000 or L4 has the same
VRAM capacity and also fits the default profile.

See RunPod's live [GPU catalog](https://www.runpod.io/gpu-models) before renting.
Price, host RAM, CUDA compatibility, and availability vary by region and change
over time; use its [pricing guidance](https://docs.runpod.io/pods/pricing) and
deployment console for the current cost. RunPod's own
[Pod selection guide](https://docs.runpod.io/pods/choose-a-pod) also recommends
choosing image-model hardware primarily by VRAM.

## What the resolution controls

The released V3 workflow defaults to **1280 x 1280**, but 1280 is not a hard
architectural limit. The web service accepts a square LayerDiff working size
from **768 through 10240 in multiples of 64**. A non-square input is centered on
a transparent square canvas and resized to that working size. LayerDiff runs an
untiled body pass and head pass at the selected size.

The UI offers 768, 1024, 1280, 1536, 2048, and 4096 as quick choices, plus a
custom field. A 10000 x 10000 source is accepted, but 10000 is not divisible by
64; use 9984 or 10048 as its LayerDiff working size. Sizes above the validated
baseline remain experimental and can exhaust GPU memory, host memory, or disk.

| UI setting | LayerDiff pixels relative to 1280 | Use it for |
| --- | ---: | --- |
| 768 | 36% | Lowest memory and fastest trial runs; least fine detail |
| 1024 | 64% | Budget GPUs or a balanced quality/speed test |
| 1280 | 100% | Released-model default and the validated baseline |
| 1536 | 144% | Modest experimental increase |
| 2048 | 256% | High-memory experimental run |
| 4096 | 1024% | Very high-memory run; validate carefully |
| 8192 | 4096% | Research-scale attempt, not a supported production target |
| 10240 | 6400% | Guardrail maximum, not a promise that the run will complete |

The percentages compare square pixel counts; they are not VRAM or runtime
promises. Model weights consume a large fixed amount of memory, and allocator
fragmentation, the GPU driver, and library versions affect the observed peak.
Lowering the resolution can rescue a marginal out-of-memory run, but it does not
make a GPU below the 8 GiB supported floor a dependable choice. The pipeline
does not tile its diffusion or attention work, so cost can grow much faster than
the output dimensions suggest. In particular, attention compute may grow more
quickly than pixel count. A 10k input being accepted only means that validation
and preprocessing support it; it does not guarantee successful 10k inference.

The Marigold depth working size is separately configurable from **256 through
2048 in multiples of 64**. Its released/default size is 768. LayerDiff and
Marigold run sequentially, so the UI bases its peak-memory estimate on the
larger normalized stage rather than adding their estimates. Seed is configurable
from 0 through 4294967295. LayerDiff steps are configurable from 1 through 100;
more steps primarily increase runtime, not the estimator's peak-VRAM range.

Uploaded files may be at most 50 MiB and 10240 pixels on either axis. These are
decoded-image validation limits, separate from the selected working sizes.

## VRAM profiles

The peak values below are planning estimates from the project's published
1280-resolution guidance. They are not guarantees for every image or host. The
Auto thresholds are intentionally more conservative than the approximate peak
so CUDA has headroom.

| Profile | Approximate peak at 1280 | Auto selection | Model cache | Tradeoff |
| --- | ---: | --- | ---: | --- |
| BF16 | 12–16 GiB | 18 GiB or more | 13.5 GiB | Unquantized model path and the normal fast choice |
| Group offload | About 10 GiB | 11 to under 18 GiB | 13.5 GiB | Moves BF16 model groups through system RAM; roughly 1.5x slower |
| NF4 | About 8 GiB | 8 to under 11 GiB | 5.7 GiB | 4-bit weights plus group offload; small quality tradeoff |
| Block swap | About 8 GiB | Never selected automatically | 13.5 GiB | BF16 blocks move between RAM and VRAM; use only when NF4 is unsuitable |

Auto refuses to start inference below 8 GiB. An explicitly selected profile is
not preflight-rejected by VRAM size, so forcing BF16 or group offload onto a
smaller GPU can still fail with CUDA out of memory. Exact 8 GiB hardware has no
margin over the published NF4/block-swap estimate: treat it as best-effort at
768, not as a guaranteed 1280 configuration. Start with Auto, and leave at least
1–2 GiB of headroom if choosing a profile manually.

The estimates describe peak capacity, not a reservation. Memory lifetime also
differs by profile: NF4 and block swap unload LayerDiff before loading Marigold,
while the normal BF16 pipeline may retain both pipeline objects. Jobs are
serialized, so capacity does not multiply with the queue length.

### How the UI estimates custom sizes

For planning only, the UI starts with the published 1280 LayerDiff and 768
Marigold ranges, then calculates:

```text
workload = max((LayerDiff size / 1280)², (Marigold size / 768)²)
low      = ceil(max(8, profile low  + 4 × (workload - 1)))
high     = ceil(max(8, profile high + 6 × (workload - 1)))
```

The profile anchors are 12–16 GiB for BF16, 9–11 GiB for group offload, and
7–9 GiB for NF4 or block swap. The displayed recommendation adds 2 GiB of
headroom at the baseline and at least 10% above an extrapolated high estimate.
For Auto, the estimate uses the profile selected from the detected capacity of
one GPU. If no GPU capacity is available, it assumes BF16 conservatively.

Example BF16 planning ranges:

| LayerDiff / Marigold | UI estimate | Interpretation |
| --- | ---: | --- |
| 1280 / 768 | 12–16 GiB | Published baseline |
| 2048 / 768 | 19–26 GiB | Area-based extrapolation |
| 4096 / 768 | 49–72 GiB | High-uncertainty extrapolation |
| 8192 / 768 | 172–256 GiB | Research-scale estimate |
| 10240 / 768 | 264–394 GiB | Exceeds even many top-end single GPUs |

These numbers are a deliberately visible heuristic, not measurements or an
allocation model. Above 1280, the uncertainty is high and the real peak can be
substantially larger. Always validate a representative image at a lower size
before increasing it.

## Which RunPod GPU to select

RunPod's current [GPU type reference](https://docs.runpod.io/references/gpu-types)
lists the models and VRAM capacities available to Pods. This image is
NVIDIA/CUDA-only; do not select an AMD GPU. Prefer Ampere or newer and avoid
V100 or older hardware. The inference path uses BF16, for which NVIDIA documents
native support on [Ampere Tensor Cores](https://www.nvidia.com/en-us/data-center/ampere-architecture/).

| Goal | Current RunPod examples | Profile and resolution |
| --- | --- | --- |
| Recommended value/default | RTX 4090, RTX 3090, RTX A5000, or L4 (24 GB) | Auto selects BF16; use 1280 |
| More headroom or datacenter features | RTX 5090 (32 GB), L40/L40S, RTX A6000, A40, or RTX 6000 Ada (48 GB) | BF16 at 1280 |
| 20 GB middle tier | RTX A4500 or RTX 4000 Ada | Auto selects BF16; use 1280 and validate the peak |
| 16 GB fallback | RTX A4000 or RTX 2000 Ada | Auto selects group offload; start at 1024, then try 1280 |
| Absolute VRAM floor | A modern 8–10 GB NVIDIA GPU, where offered | Auto selects NF4; start at 768 or 1024 |
| Experimental 4096 work | A100 80 GB, H100 80 GB, H200 141 GB, or B200 180 GB | BF16 estimate is 49–72 GiB; leave headroom |
| Very high resolution research | H200, B200, or another very large single GPU | Still untiled and unvalidated; use the estimate, not aggregate VRAM |

The service reports every attached GPU, but the current worker runs one serial
job on the default CUDA device. It does not implement model parallelism, FSDP,
DDP, or a multi-worker scheduler. An 8-GPU machine therefore does **not** provide
one job with eight GPUs' combined VRAM and the other seven GPUs remain unused.
Do not pay for multiple GPUs for the current application.

The 8 GiB tier is a compatibility floor, not the preferred rental. There is
almost no margin for allocator variation at the published 1280 peak. A 16 GiB
Pod is a reasonable budget option, while a 24 GiB Pod avoids offload and is the
safest default. Very large A100/H100-class GPUs only make sense when their
availability, attached resources, or organizational requirements justify their
cost; this pipeline cannot use their extra memory across concurrent jobs.

RunPod lists B300 instances with 288 GB per GPU and up to eight GPUs, but the
current runtime cannot pool those devices. B300 is also **not validated with
this image**: the image contains CUDA 12.8, while NVIDIA introduced native
`sm_103` target support in CUDA 12.9. Do not select B300 until the image and its
pinned PyTorch stack have been upgraded and tested for it. See RunPod's
[B300 page](https://www.runpod.io/gpu-models/b300) and NVIDIA's
[CUDA feature archive](https://docs.nvidia.com/cuda/archive/13.0.1/cuda-features-archive/index.html).

For supported GPUs, use RunPod's **Additional filters → CUDA Versions** control
to choose a compatible host, as described in its
[Pod management guide](https://docs.runpod.io/pods/manage-pods).

## System RAM and disk

Offload modes reduce VRAM by keeping or moving more weights in ordinary system
RAM. As planning targets, choose at least 32 GiB of system RAM for BF16 or NF4
and 48 GiB for group offload or block swap. These are conservative deployment
targets rather than enforced minimums. Check the exact host configuration in
RunPod before renting; GPU models with the same VRAM can be paired with different
amounts of RAM.

The pinned model-cache sizes are:

| Downloaded bundles | Cache size |
| --- | ---: |
| NF4 only | About 5.7 GiB |
| BF16 only | About 13.5 GiB |
| Both | About 19.2 GiB |

The downloader also requires 5 GiB of free safety space before it starts. The
container image is checked in CI against a 12 GiB unpacked-root-filesystem limit,
and PSDs, PNG layers, depth maps, logs, and ZIPs accumulate per job. Use 60 GB of
container disk for a normal single-bundle Pod and consider 80 GB or a persistent
volume if switching between bundles or retaining many jobs. As a derived planning
allowance rather than a measured limit, budget roughly 0.5–1 GiB per retained
1280 job; image content and compression can move the actual size substantially.

For experimental sizes, scale host resources aggressively. A reasonable
starting plan—not a measured requirement—is 48–64 GiB RAM for 2048, 64–128 GiB
for 4096, and at least 128 GiB for 8192 or larger. A 10k run creates several
100-million-pixel intermediate arrays plus PSD, PNG, and ZIP copies; allow tens
of GiB of free job storage and expect the archive step to temporarily duplicate
data. Persistent output retention needs additional space. Monitor both memory
and disk during the first representative run.

## Measure a real run

Estimates should be validated with the images and settings that matter to you.
Over SSH, monitor a representative run with:

```bash
nvidia-smi \
  --query-gpu=name,memory.total,memory.used,utilization.gpu \
  --format=csv \
  --loop=1
```

In a second shell, watch host memory and storage:

```bash
while sleep 2; do
  grep -E 'MemTotal|MemAvailable' /proc/meminfo
  df -h /home/seethrough /workspace 2>/dev/null
done
```

Record the maximum `memory.used`, leave headroom for allocator variation, and
repeat after changing the resolution or memory profile. The UI and
`http://127.0.0.1:4321/api/system` also show detected GPU capacity and free disk.
