FROM --platform=linux/amd64 public.ecr.aws/lambda/python:3.10-x86_64

# Install Chrome and dependencies
RUN yum update -y && \
    yum install -y wget unzip && \
    wget https://dl.google.com/linux/direct/google-chrome-stable_current_x86_64.rpm && \
    yum install -y ./google-chrome-stable_current_x86_64.rpm && \
    rm google-chrome-stable_current_x86_64.rpm && \
    yum install -y libX11 libXcomposite libXcursor libXdamage libXext \
        libXi libXtst cups-libs libXScrnSaver libXrandr alsa-lib pango \
        atk at-spi2-atk gtk3 google-noto-cjk-fonts && \
    yum clean all && \
    rm -rf /var/cache/yum

# Install emoji font support
RUN wget -O noto-emoji.rpm https://kojipkgs.fedoraproject.org//packages/google-noto-emoji-fonts/20200916/2.fc35/noarch/google-noto-emoji-fonts-20200916-2.fc35.noarch.rpm && \
    yum install -y noto-emoji.rpm && \
    rm noto-emoji.rpm

# Install ChromeDriver matching Chrome version
RUN CHROME_VERSION=$(google-chrome --version | awk '{print $3}' | cut -d. -f1) && \
    CHROME_DRIVER_VERSION=$(wget -qO- https://chromedriver.storage.googleapis.com/LATEST_RELEASE_${CHROME_VERSION}) && \
    wget https://chromedriver.storage.googleapis.com/${CHROME_DRIVER_VERSION}/chromedriver_linux64.zip && \
    unzip chromedriver_linux64.zip -d /usr/local/bin/ && \
    rm chromedriver_linux64.zip && \
    chmod +x /usr/local/bin/chromedriver

# Set environment variables
ENV PATH="/usr/local/bin:${PATH}"
ENV CHROME_BIN=/usr/bin/google-chrome
ENV CHROME_PATH=/usr/lib/chrome
ENV PYTHONPATH=${LAMBDA_TASK_ROOT}

# Copy application files
COPY main.py requirements.txt ./
COPY configs/ configs/
COPY templates/ templates/
COPY src/ src/

# Install Python dependencies
RUN python3.10 -m pip install --no-cache-dir -r requirements.txt -t .

# Create and set permissions for Chrome temporary directory
RUN mkdir -p /tmp/chromium && chmod 777 /tmp/chromium

# Configure font preferences for emoji support
RUN mkdir -p /etc/fonts/conf.d && \
    echo '<?xml version="1.0"?>\
    <!DOCTYPE fontconfig SYSTEM "fonts.dtd">\
    <fontconfig>\
      <alias>\
        <family>sans-serif</family>\
        <prefer>\
          <family>Noto Sans CJK</family>\
          <family>Noto Color Emoji</family>\
        </prefer>\
      </alias>\
    </fontconfig>' > /etc/fonts/conf.d/99-emoji.conf && \
    fc-cache -fv || echo "Font cache update failed but continuing anyway"

CMD ["main.handler"]
