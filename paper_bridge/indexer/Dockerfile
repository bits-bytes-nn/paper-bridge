FROM --platform=linux/amd64 ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Seoul
ENV PYTHONPATH=/app:/app/graphrag-toolkit/src
ENV AWS_DEFAULT_REGION=us-west-2

RUN apt-get update && \
    apt-get install -y \
    python3.10 \
    python3-pip \
    git \
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-eng \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxrender1 \
    libxext6 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN git clone https://github.com/awslabs/graphrag-toolkit.git && \
    cd graphrag-toolkit && \
    git checkout v2.0.1

RUN cd /app/graphrag-toolkit && \
    pip install -r src/requirements.txt

COPY requirements.txt ./
RUN python3 -m pip install --no-cache-dir -r requirements.txt

RUN SITE_PACKAGES=$(python3 -c "import site; print(site.getsitepackages()[0])") && \
    ln -s /app/graphrag-toolkit/src/graphrag_toolkit $SITE_PACKAGES/graphrag_toolkit

COPY main.py ./
COPY configs/ paper_bridge/indexer/configs/
COPY src/ paper_bridge/indexer/src/

CMD ["python3", "main.py"]
