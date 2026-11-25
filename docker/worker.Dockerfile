FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir . \
    && groupadd -g 10001 mesh \
    && useradd -u 10001 -g 10001 -s /usr/sbin/nologin mesh \
    && mkdir -p /workspace \
    && chown -R mesh:mesh /workspace

# 15.4.1: ship the opencode coding agent binary so --harness opencode can
# spawn inside the worker. Installer drops into $HOME/.opencode/bin; HOME is
# redirected so the fixed path survives the ${UID:-1000} runtime user
# (rootfs is read_only, /tmp is tmpfs — nothing may live under $HOME then).
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates openssh-client \
    && rm -rf /var/lib/apt/lists/* \
    && HOME=/usr/local sh -c 'curl -fsSL https://opencode.ai/install | bash' \
    && ln -s /usr/local/.opencode/bin/opencode /usr/local/bin/opencode \
    && chmod -R a+rX /usr/local/.opencode

# The compose stack runs as ${UID:-1000}:${GID:-1000} (host user). ssh(1)
# refuses to start without a passwd entry for that uid, so give it one.
RUN groupadd -g 1000 hostuser \
    && useradd -u 1000 -g 1000 -M -s /usr/sbin/nologin hostuser

USER 10001:10001

ENV WORKSPACE_ROOT=/workspace

CMD ["python", "-m", "memnc.worker"]
