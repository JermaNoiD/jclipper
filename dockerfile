# Stage 1: Build FFmpeg
FROM ubuntu:22.04 AS ffmpeg-builder

# Install curl, xz-utils, and ca-certificates for downloading and extracting FFmpeg
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    xz-utils \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Download pre-built FFmpeg static binary (includes ffprobe)
WORKDIR /tmp
RUN curl -L https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz | tar -xJ && \
    mv ffmpeg-*-static/ffmpeg /usr/local/bin/ffmpeg && \
    mv ffmpeg-*-static/ffprobe /usr/local/bin/ffprobe && \
    chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe

# Stage 2: Build Python dependencies
FROM python:3.12-slim AS python-builder

# Set working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --target=/install -r requirements.txt && \
    find /install -name 'gunicorn' -type f -exec cp {} /install/bin/ \; && \
    chmod +x /install/bin/gunicorn

# Stage 3: Runtime image
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Copy Python dependencies and gunicorn binary from python-builder stage
COPY --from=python-builder /install /usr/local/lib/python3.12/site-packages
COPY --from=python-builder /install/bin/gunicorn /usr/local/bin/gunicorn
ENV PYTHONPATH=/usr/local/lib/python3.12/site-packages

# Copy application code
COPY app/ .

# Copy FFmpeg binaries from ffmpeg-builder stage
COPY --from=ffmpeg-builder /usr/local/bin/ffmpeg /usr/local/bin/
COPY --from=ffmpeg-builder /usr/local/bin/ffprobe /usr/local/bin/

# Set default environment variables (can be overridden with docker run)
ENV MOVIES_DIR=/movies
ENV TMP_DIR=/tmp/output
ENV VIDEO_EXTENSIONS=mp4,mkv,avi,mov,wmv,flv

# Expose port
EXPOSE 5000

# Run with gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "app:app"]