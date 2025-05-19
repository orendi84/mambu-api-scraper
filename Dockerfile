# Stage 2: Main application image
FROM python:3.10-slim

# Set environment variables for Chrome and Python
ENV APP_HOME=/app
ENV CHROME_VERSION=stable
ENV RUNNING_IN_DOCKER=true
# For debian-based: google-chrome-stable, google-chrome-beta, google-chrome-dev, google-chrome-unstable
# We'll install stable. undetected-chromedriver will use the system chrome.

# Python best practices
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Create app directory
RUN mkdir -p $APP_HOME
WORKDIR $APP_HOME

# Install system dependencies, Chromium browser + driver, xvfb, xauth, and common missing libs for headless Chrome
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    gnupg \
    procps \
    chromium \
    chromium-driver \
    xvfb \
    xauth \
    # Common dependencies for headless Chrome
    libglib2.0-0 \
    libnss3 \
    libgconf-2-4 \
    libfontconfig1 \
    libfontconfig1-dev \
    libfreetype6-dev \
    libjpeg-dev \
    libpng-dev \
    libx11-6 \
    libx11-xcb1 \
    libxcb-dri3-0 \
    libxcomposite1 \
    libxcursor1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxi6 \
    libxrandr2 \
    libxrender1 \
    libxss1 \
    libxtst6 \
    ca-certificates \
    fonts-liberation \
    libappindicator3-1 \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libc6 \
    libcairo2 \
    libcups2 \
    libdbus-1-3 \
    libexpat1 \
    libgbm1 \
    libgcc1 \
    libnspr4 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libstdc++6 \
    libu2f-udev \
    libvulkan1 \
    libxshmfence1 \
    lsb-release \
    xdg-utils \
    # Clean up
    && rm -rf /var/lib/apt/lists/*

# Verify paths and check for missing shared libraries (for debugging build)
RUN echo "--- Verifying Chrome and ChromeDriver paths ---" && \
    which chromium && \
    which chromedriver && \
    ls -l /usr/bin/chromium /usr/bin/chromedriver && \
    echo "--- Checking ldd for chromium (first 20 lines) ---" && \
    (ldd /usr/bin/chromium | head -n 20 || true) && \
    echo "--- Checking ldd for chromedriver (first 20 lines) ---" && \
    (ldd /usr/bin/chromedriver | head -n 20 || true) && \
    which xvfb-run && \
    which xauth # Verify xauth is installed and in PATH

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir gunicorn==22.0.0

# Copy the application script
COPY mambu_scraper_experimental.py .

# Create pdf_downloads directory and set permissions (if script writes there inside container)
RUN mkdir -p ${APP_HOME}/pdf_downloads && chmod -R 777 ${APP_HOME}/pdf_downloads
RUN mkdir -p ${APP_HOME}/output && chmod -R 777 ${APP_HOME}/output
RUN mkdir -p /tmp && chmod -R 777 /tmp # For chromedriver.log and xvfb_error.log

# CMD for running with Gunicorn in Cloud Run, using xvfb-run
# (Reverting to the Gunicorn CMD, as the sleep/echo CMD was for diagnosing xvfb-run)
CMD xvfb-run --auto-servernum --server-args="-screen 0 1920x1080x24" gunicorn --bind "0.0.0.0:$PORT" --workers 1 --threads 8 --timeout 0 mambu_scraper_experimental:app

# Example of how to run with all args if needed (for old entrypoint):
# CMD ["--start_url", "https://support.mambu.com/docs", "--output_dir", "./output", "--log_level", "INFO"]
# For Cloud Run, these args will be part of the "Args" configuration for the container instance. 