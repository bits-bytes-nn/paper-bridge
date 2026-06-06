# syntax=docker/dockerfile:1
#
# Build context: the REPO ROOT (buildspec runs `docker build -f paper_bridge/summarizer/Dockerfile .`),
# so that the shared package (paper_bridge/shared/) is included in the image.
#
# Multi-stage build:
#   1. builder  -- installs build toolchain and pip-installs all Python deps into
#                  a self-contained venv (/opt/venv), including graphrag-lexical-graph (v3)
#                  pulled from the same pinned git tag used in pyproject.toml.
#   2. runtime  -- Ubuntu image with Chrome + Korean fonts/locales for Selenium,
#                  the prebuilt venv and the application code, run as a non-root user.

############################
# Stage 1: builder
############################
ARG TARGET_PLATFORM=linux/amd64
FROM --platform=${TARGET_PLATFORM} public.ecr.aws/ubuntu/ubuntu:22.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

# Build-only dependencies (compilers, headers) live only in this stage.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        python3.11 \
        python3.11-venv \
        python3.11-dev \
        python3-pip \
        build-essential \
        git \
        ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Create an isolated virtual environment for all Python packages.
RUN python3.11 -m venv "$VIRTUAL_ENV" && \
    pip install --upgrade pip setuptools wheel

# Install the CPU-only build of torch FIRST (see indexer Dockerfile for rationale):
# graphrag v3's stack (sentence-transformers / FlagEmbedding) pulls torch, and the
# default GPU wheels add multi-GB CUDA/NCCL libs the workload never uses.
RUN pip install --index-url https://download.pytorch.org/whl/cpu \
    "torch==2.4.1" "torchvision==0.19.1"

# Install application Python dependencies first (cached across app code changes).
COPY paper_bridge/summarizer/requirements.txt ./
RUN pip install -r requirements.txt

# Install graphrag v3 (renamed to graphrag-lexical-graph) from the pinned
# v3.18.3 tag's `lexical-graph` subdirectory, matching pyproject.toml. The
# importable package name is still `graphrag_toolkit.lexical_graph`.
RUN pip install "git+https://github.com/awslabs/graphrag-toolkit.git@graphrag-lexical-graph/v3.18.3#subdirectory=lexical-graph"

############################
# Stage 2: runtime
############################
FROM --platform=${TARGET_PLATFORM} public.ecr.aws/ubuntu/ubuntu:22.04 AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Seoul \
    LANG=ko_KR.UTF-8 \
    LC_ALL=ko_KR.UTF-8 \
    CHROME_BIN=/usr/bin/google-chrome \
    CHROME_PATH=/usr/lib/google-chrome \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

# Runtime-only OS dependencies: Python interpreter, headless display (xvfb),
# locales and Korean fonts for rendering, and Chrome support libraries.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        python3.11 \
        wget \
        gnupg2 \
        ca-certificates \
        git \
        xvfb \
        libxi6 \
        libgconf-2-4 \
        locales \
        language-pack-ko \
        fontconfig \
        fonts-nanum \
        fonts-noto-cjk \
        fonts-noto-color-emoji \
        fonts-nanum-coding \
        fonts-nanum-extra && \
    locale-gen ko_KR.UTF-8 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install Google Chrome (stable) for Selenium.
RUN wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - && \
    echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends google-chrome-stable && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Configure font preferences for CJK rendering.
RUN mkdir -p /etc/fonts/conf.d && \
    echo '<?xml version="1.0"?>\
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\
<fontconfig>\
  <alias>\
    <family>sans-serif</family>\
    <prefer>\
      <family>Noto Sans CJK KR</family>\
      <family>Nanum Gothic</family>\
      <family>Noto Color Emoji</family>\
    </prefer>\
  </alias>\
  <alias>\
    <family>serif</family>\
    <prefer>\
      <family>Noto Serif CJK</family>\
      <family>Nanum Myeongjo</family>\
    </prefer>\
  </alias>\
  <alias>\
    <family>monospace</family>\
    <prefer>\
      <family>Nanum Gothic Coding</family>\
      <family>Noto Sans Mono CJK</family>\
    </prefer>\
  </alias>\
</fontconfig>' > /etc/fonts/conf.d/64-language-selector-prefer.conf && \
    fc-cache -fv

# Copy the prebuilt virtual environment from the builder stage.
COPY --from=builder /opt/venv /opt/venv

# Create and switch to an unprivileged user.
RUN useradd --create-home --shell /bin/bash appuser

WORKDIR /app

# Application source (copied last for better layer caching).
COPY paper_bridge/summarizer/main.py ./
COPY paper_bridge/shared/ paper_bridge/shared/
COPY paper_bridge/summarizer/configs/ paper_bridge/summarizer/configs/
COPY paper_bridge/summarizer/src/ paper_bridge/summarizer/src/
COPY paper_bridge/summarizer/templates/ paper_bridge/summarizer/templates/

RUN chown -R appuser:appuser /app
USER appuser

# Verify the venv interpreter and ALL runtime-critical backends import cleanly
# together. graphrag v3 (graphrag_toolkit) + bedrock_converse are co-imported on
# purpose: their botocore pin windows must remain compatible (v3 relaxed botocore
# to >=1.40.61), so co-importing them catches a real break here (not silently
# green). The image-block primitives are imported too, since figure analysis now
# routes images through BedrockConverse ChatMessage blocks (no BedrockMultiModal).
HEALTHCHECK --interval=5m --timeout=10s --start-period=30s --retries=3 \
    CMD python3 -c "import graphrag_toolkit.lexical_graph, selenium, llama_index; from llama_index.llms.bedrock_converse import BedrockConverse; from llama_index.core.llms import ChatMessage, ImageBlock, TextBlock; from llama_index.vector_stores.opensearch import OpensearchVectorStore" || exit 1

CMD ["python3", "main.py"]
