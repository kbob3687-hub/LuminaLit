# paperharness 基础镜像（契约 1.3：非 root uid 1000）
# 构建：docker build -t paperharness/base-py3.11 -f docker/base-py3.11.Dockerfile docker/
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 runner \
    && mkdir /workspace \
    && chown runner:runner /workspace

# 专用 venv 归非 root 用户：pip 装的包和 bin 脚本都落在 venv 内。
# 原因：python -I（靶场防 shadow import）忽略 user site，包必须落在
# -I 可见的 venv site-packages，而不是 ~/.local 或 root 的系统目录。
RUN python -m venv /opt/venv \
    && chown -R runner:runner /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

USER runner
WORKDIR /workspace

# 沙箱 workspace 从宿主机挂载，属主不固定，统一放行 dubious ownership 检查
RUN git config --global --add safe.directory "*"
