# syntax=docker/dockerfile:1
#
# Build context: the REPO ROOT (buildspec runs `docker build -f paper_bridge/indexer/Dockerfile .`),
# so that the shared package (paper_bridge/shared/) is included in the image.
#
# Multi-stage build:
#   1. builder  -- installs build toolchain and pip-installs all Python deps into
#                  a self-contained venv (/opt/venv), including graphrag-lexical-graph (v3)
#                  pulled from the same pinned git tag used in pyproject.toml.
#   2. runtime  -- slim Ubuntu image with only the runtime OS libraries plus the
#                  prebuilt venv and application code, running as a non-root user.

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

# Install the CPU-only build of torch FIRST, from PyTorch's CPU wheel index, so
# the transitive torch pulled by unstructured-inference / sentence-transformers /
# graphrag is already satisfied by a CPU build. This avoids the multi-GB CUDA/NCCL
# GPU wheels (the image ran out of disk and the Batch job OOM'd with the GPU build);
# the workload has no GPU, so CPU torch is correct and much smaller.
RUN pip install --index-url https://download.pytorch.org/whl/cpu \
    "torch==2.4.1" "torchvision==0.19.1"

# Install application Python dependencies first (cached across app code changes).
COPY paper_bridge/indexer/requirements.txt ./
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
    AWS_DEFAULT_REGION=us-west-2 \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

# Runtime-only OS libraries: Python interpreter, poppler (pdf2image),
# tesseract OCR, and the OpenGL/X libs required by unstructured-inference.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        python3.11 \
        poppler-utils \
        tesseract-ocr \
        tesseract-ocr-eng \
        libgl1-mesa-glx \
        libglib2.0-0 \
        libsm6 \
        libxrender1 \
        libxext6 \
        ca-certificates && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Copy the prebuilt virtual environment from the builder stage.
COPY --from=builder /opt/venv /opt/venv

# Create and switch to an unprivileged user.
RUN useradd --create-home --shell /bin/bash appuser

WORKDIR /app

# Application source (copied last for better layer caching).
COPY paper_bridge/indexer/main.py ./
COPY paper_bridge/shared/ paper_bridge/shared/
COPY paper_bridge/indexer/configs/ paper_bridge/indexer/configs/
COPY paper_bridge/indexer/src/ paper_bridge/indexer/src/

RUN chown -R appuser:appuser /app
USER appuser

# Verify the venv interpreter and core deps import cleanly. graphrag v3
# (graphrag_toolkit.lexical_graph) + bedrock_converse are co-imported to catch a
# botocore-pin regression early (v3 relaxed botocore to >=1.40.61). The OpenSearch
# vector store is co-imported too: graphrag loads it lazily for "aoss://" stores,
# so a missing llama-index-vector-stores-opensearch only surfaces at graph-build
# time in prod unless checked here.
HEALTHCHECK --interval=5m --timeout=10s --start-period=30s --retries=3 \
    CMD python3 -c "import graphrag_toolkit.lexical_graph, llama_index; from llama_index.llms.bedrock_converse import BedrockConverse; from llama_index.vector_stores.opensearch import OpensearchVectorStore" || exit 1

CMD ["python3", "main.py"]
