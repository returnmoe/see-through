# syntax=docker/dockerfile:1.12

ARG NODE_IMAGE=node:24-bookworm-slim@sha256:6f7b03f7c2c8e2e784dcf9295400527b9b1270fd37b7e9a7285cf83b6951452d
ARG CUDA_IMAGE=nvidia/cuda:12.8.1-base-ubuntu24.04@sha256:e711c99333fdfe8ae1e677b4972be6c5021f0128a1d31f775c7e58d88921b6a9

FROM --platform=$BUILDPLATFORM ${NODE_IMAGE} AS web-build
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --ignore-scripts
COPY web/ ./
RUN npm run build

FROM ${CUDA_IMAGE} AS runtime-base

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        libglib2.0-0t64 \
        libgomp1 \
        openssh-server \
        passwd \
        python3 \
        python3-venv \
        tini \
        util-linux \
    && rm -f /etc/ssh/ssh_host_* \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv \
    && (getent group video >/dev/null || groupadd --system video)
RUN useradd --uid 10001 --create-home --shell /bin/bash --groups video seethrough \
    && install -d -m 0755 /opt/see-through /usr/local/libexec/see-through \
    && install -d -m 0750 -o seethrough -g seethrough \
        /home/seethrough/data /home/seethrough/.cache/huggingface

ENV PATH=/opt/venv/bin:/usr/local/bin:/usr/bin:/bin \
    PYTHONPATH=/opt/see-through/common:/opt/see-through/server \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    SEE_THROUGH_BIND_HOST=127.0.0.1 \
    SEE_THROUGH_PORT=4321 \
    SEE_THROUGH_DATA_DIR=/home/seethrough/data \
    SEE_THROUGH_MODEL_CACHE=/home/seethrough/.cache/huggingface \
    SEE_THROUGH_PREFETCH=auto \
    HF_HOME=/home/seethrough/.cache/huggingface

COPY docker/entrypoint.sh /usr/local/bin/see-through-entrypoint
COPY docker/see-through /usr/local/bin/see-through
COPY docker/validate_authorized_keys.py docker/listener_contract.py docker/write_runtime_env.py docker/healthcheck.py /usr/local/libexec/see-through/
COPY docker/see-through-profile.sh /etc/profile.d/see-through.sh
RUN chmod 0755 \
        /usr/local/bin/see-through-entrypoint \
        /usr/local/bin/see-through \
        /usr/local/libexec/see-through/*.py \
    && chmod 0644 /etc/profile.d/see-through.sh

# Small, GPU-free image used by CI to exercise the exact production entrypoint,
# sshd policy, static UI, and API startup without constructing the ML layers.
FROM runtime-base AS contract-test
COPY requirements-image/runtime-resolved.lock /tmp/runtime-resolved.lock
RUN python -m pip install --constraint /tmp/runtime-resolved.lock \
        fastapi==0.139.0 \
        huggingface-hub==1.7.2 \
        pillow==12.1.1 \
        pillow-jxl-plugin==1.3.7 \
        python-multipart==0.0.32 \
        uvicorn==0.51.0 \
    && rm -f /tmp/runtime-resolved.lock
COPY server/seethrough_server/ /opt/see-through/server/seethrough_server/
COPY common/assets/ /opt/see-through/common/assets/
COPY --from=web-build /build/web/dist/ /opt/see-through/web/dist/
WORKDIR /opt/see-through
EXPOSE 22
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["/usr/local/libexec/see-through/healthcheck.py"]
ENTRYPOINT ["/usr/bin/tini", "-g", "--", "/usr/local/bin/see-through-entrypoint"]

# Each large binary family is installed in its own bounded layer. --no-deps is
# deliberate: the complete dependency set is explicitly represented here.
FROM runtime-base AS gpu-runtime
COPY requirements-image/ /tmp/requirements-image/
RUN python -m pip install --no-deps --require-hashes \
        -r /tmp/requirements-image/cuda-core.lock
RUN python -m pip install --no-deps --require-hashes \
        -r /tmp/requirements-image/cuda-cudnn.lock
RUN python -m pip install --no-deps --require-hashes \
        -r /tmp/requirements-image/cuda-cublas.lock
RUN python -m pip install --no-deps --require-hashes \
        -r /tmp/requirements-image/cuda-solvers.lock
RUN python -m pip install --no-deps --require-hashes \
        -r /tmp/requirements-image/cuda-sparse.lock
RUN python -m pip install --no-deps --require-hashes \
        -r /tmp/requirements-image/cuda-collectives.lock
RUN python -m pip install --no-deps --require-hashes \
        -r /tmp/requirements-image/torch.lock
RUN python -m pip install --no-deps --require-hashes \
        -r /tmp/requirements-image/torchvision.lock
RUN python -m pip install \
        --constraint /tmp/requirements-image/runtime-resolved.lock \
        -r /tmp/requirements-image/runtime.txt \
    && python -m pip check \
    && rm -rf /tmp/requirements-image

FROM gpu-runtime AS runtime

ARG VCS_REF=unknown
ARG VERSION=development
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.title="See-through RunPod" \
      org.opencontainers.image.description="Loopback-first single-image See-through inference for RunPod" \
      org.opencontainers.image.source="https://github.com/returnmoe/see-through" \
      org.opencontainers.image.url="https://github.com/returnmoe/see-through" \
      org.opencontainers.image.documentation="https://github.com/returnmoe/see-through/blob/master/docs/runpod.md" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.revision="$VCS_REF" \
      org.opencontainers.image.version="$VERSION" \
      org.opencontainers.image.created="$BUILD_DATE"

WORKDIR /opt/see-through
COPY common/modules/ /opt/see-through/common/modules/
COPY common/utils/ /opt/see-through/common/utils/
COPY common/assets/ /opt/see-through/common/assets/
COPY inference/scripts/inference_psd.py /opt/see-through/inference/scripts/inference_psd.py
COPY inference/scripts/inference_psd_quantized.py /opt/see-through/inference/scripts/inference_psd_quantized.py
COPY inference/scripts/inference_psd_blockswap.py /opt/see-through/inference/scripts/inference_psd_blockswap.py
COPY server/seethrough_server/ /opt/see-through/server/seethrough_server/
COPY LICENSE /opt/see-through/LICENSE
COPY --from=web-build /build/web/dist/ /opt/see-through/web/dist/
RUN ln -s common/assets /opt/see-through/assets \
    && HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 DIFFUSERS_OFFLINE=1 \
       python -c "import torch, torchvision; assert torch.__version__ == '2.8.0+cu128'; assert torchvision.__version__ == '0.23.0+cu128'; assert torch.version.cuda == '12.8'" \
    && HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 DIFFUSERS_OFFLINE=1 \
       python inference/scripts/inference_psd.py --help >/dev/null \
    && HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 DIFFUSERS_OFFLINE=1 \
       python inference/scripts/inference_psd_quantized.py --help >/dev/null \
    && HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 DIFFUSERS_OFFLINE=1 \
       python inference/scripts/inference_psd_blockswap.py --help >/dev/null \
    && ! find /opt/see-through -type f \( -name '*.bin' -o -name '*.ckpt' -o -name '*.pt' -o -name '*.pth' -o -name '*.safetensors' \) -print -quit | grep -q . \
    && test -z "$(find /home/seethrough/.cache/huggingface -mindepth 1 -print -quit)" \
    && test ! -e /root/.cache/huggingface \
    && test "$(du -sx --block-size=1 / | cut -f1)" -le 12884901888

EXPOSE 22
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["/usr/local/libexec/see-through/healthcheck.py"]
ENTRYPOINT ["/usr/bin/tini", "-g", "--", "/usr/local/bin/see-through-entrypoint"]
