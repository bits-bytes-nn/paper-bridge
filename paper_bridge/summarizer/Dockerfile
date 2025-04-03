FROM --platform=linux/amd64 public.ecr.aws/ubuntu/ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Seoul \
    LANG=ko_KR.UTF-8 \
    LC_ALL=ko_KR.UTF-8 \
    CHROME_BIN=/usr/bin/google-chrome \
    CHROME_PATH=/usr/lib/google-chrome \
    PYTHONPATH=/app:/app/graphrag-toolkit/src

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        python3.10 \
        python3-pip \
        wget \
        gnupg2 \
        ca-certificates \
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
        fonts-nanum-extra \
        git && \
    locale-gen ko_KR.UTF-8 && \
    ln -sf /usr/bin/python3 /usr/bin/python && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - && \
    echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends google-chrome-stable && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY main.py requirements.txt ./
COPY configs/ paper_bridge/summarizer/configs/
COPY src/ paper_bridge/summarizer/src/
COPY templates/ paper_bridge/summarizer/templates/

RUN python3 -m pip install --no-cache-dir -r requirements.txt

RUN git clone https://github.com/awslabs/graphrag-toolkit.git && \
    cd graphrag-toolkit && \
    git checkout v2.2.1 && \
    python3 -m pip install --no-cache-dir -r src/requirements.txt && \
    SITE_PACKAGES=$(python3 -c "import site; print(site.getsitepackages()[0])") && \
    ln -sf /app/graphrag-toolkit/src/graphrag_toolkit $SITE_PACKAGES/graphrag_toolkit

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

CMD ["python3", "main.py"]