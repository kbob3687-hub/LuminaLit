# paperharness 基础镜像（契约 1.3：非 root uid 1000）
# 构建：docker build -t paperharness/base-py3.10 -f docker/base-py3.10.Dockerfile docker/
FROM python:3.10-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 runner \
    && mkdir /workspace \
    && chown runner:runner /workspace

USER runner
WORKDIR /workspace

# 沙箱 workspace 从宿主机挂载，属主不固定，统一放行 dubious ownership 检查
RUN git config --global --add safe.directory "*"
