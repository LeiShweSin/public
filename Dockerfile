FROM arm32v7/debian:bullseye-slim

# --- Basic tools ---
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    udev \
    libglib2.0-0 \
    libxkbcommon0 \
    libdrm2 \
    libexif12 \
    libjpeg62-turbo \
    && rm -rf /var/lib/apt/lists/*

# --- Add Raspberry Pi repo ---
RUN wget -qO - https://archive.raspberrypi.org/debian/raspberrypi.gpg.key | apt-key add - && \
    echo "deb http://archive.raspberrypi.org/debian/ bullseye main" > /etc/apt/sources.list.d/raspi.list

# --- Install dependencies (camera, GPIO, OpenCV, zbar, etc) ---
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-dev \
    build-essential \
    python3-setuptools \
    python3-wheel \
    gfortran \
    libopenblas-dev \
    liblapack-dev \
    libcamera0 \
    libcamera-apps \
    python3-picamera2 \
    python3-opencv \
    python3-pyzbar \
    libzbar0 \
    v4l-utils \
    i2c-tools \
    python3-smbus \
    python3-spidev \
    python3-rpi.gpio \
    sqlite3 \
    git \
    libjpeg62-turbo \
    libpng16-16 \
    libtiff5 \
    libopenjp2-7 \
    libatlas-base-dev \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# --- Set working directory ---
WORKDIR /app

# --- Copy app code ---
COPY . .

# --- Copy entrypoint.sh and make it executable ---
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# --- Install Python dependencies ---
RUN pip3 install --no-cache-dir -r requirements.txt

# --- Optional: log level for camera debugging ---
ENV LIBCAMERA_LOG_LEVELS=3

# --- Set the entrypoint ---
ENTRYPOINT ["/entrypoint.sh"]

# --- Expose port for online platform ---
EXPOSE 5000
