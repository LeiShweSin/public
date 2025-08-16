FROM arm32v7/debian:bullseye-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=UTF-8 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    LIBCAMERA_LOG_LEVELS=3

# --- Base tools and HTTPS certs ---
RUN apt-get update --fix-missing && apt-get install -y \
    ca-certificates \
    wget \
    curl \
    gnupg \
    udev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# --- Add Raspberry Pi repository (needed for picamera2/libcamera-apps) ---
RUN wget -qO - https://archive.raspberrypi.org/debian/raspberrypi.gpg.key | apt-key add - && \
    echo "deb http://archive.raspberrypi.org/debian/ bullseye main" > /etc/apt/sources.list.d/raspi.list

# --- System & runtime deps (camera, zbar, GPIO/I2C/SPI, OpenCV, Node) ---
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-dev \
    python3-setuptools \
    python3-wheel \
    gfortran \
    libopenblas-dev \
    liblapack-dev \
    libcamera0 \
    libcamera-apps \
    python3-picamera2 \
    libopencv-dev \
    python3-opencv \
    libzbar0 \
    python3-pyzbar \
    v4l-utils \
    i2c-tools \
    python3-smbus \
    python3-spidev \
    python3-rpi.gpio \
    sqlite3 \
    git \
    nodejs \
    npm \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# --- App working dir ---
WORKDIR /app

# Copy only the app directory contents into /app
COPY app/ /app/

# Copy requirements.txt from project root (sibling of app/)
COPY requirements.txt /requirements.txt

# Avoid heavy wheels via pip: strip packages we install via apt
RUN sed -i -e '/opencv/d' -e '/pyzbar/d' -e '/RPi.GPIO/d' /requirements.txt

# Python libs (lightweight ones from your requirements)
RUN pip3 install --no-cache-dir -r /requirements.txt

# Node dependencies for server.js (uses app/package.json)
RUN npm install --omit=dev

# --- SPI-Py install (sibling dir at build time) ---
COPY SPI-Py/ /SPI-Py/
WORKDIR /SPI-Py
RUN python3 setup.py install

# --- Back to /app and setup entrypoint ---
WORKDIR /app
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 5000 8080
ENTRYPOINT ["/entrypoint.sh"]
