# Stage 1: Build Python dependencies
FROM python:3.12-slim AS python-builder

# Set working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --target=/install -r requirements.txt && \
    find /install -name 'gunicorn' -type f -exec cp {} /install/bin/ \; && \
    chmod +x /install/bin/gunicorn

# Stage 2: Runtime image
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install FFmpeg from the distribution packages. Unlike the old static build,
# this includes hardware acceleration support (NVENC/NVDEC), so previews can be
# rendered on the GPU when one is available. The app falls back to software
# encoding (libx264) automatically when no GPU is present.
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy Python dependencies and gunicorn binary from python-builder stage
COPY --from=python-builder /install /usr/local/lib/python3.12/site-packages
COPY --from=python-builder /install/bin/gunicorn /usr/local/bin/gunicorn
ENV PYTHONPATH=/usr/local/lib/python3.12/site-packages

# Copy application code
COPY app/ .

# Set default environment variables (can be overridden with docker run)
ENV MOVIES_DIR=/movies
ENV TEMP_DIR=/tmp/jclipper
ENV VIDEO_EXTENSIONS=mp4,mkv,avi,mov,wmv,flv

# Expose port
EXPOSE 5000

# Run with gunicorn - single worker with threads, since the app uses in-process
# state (active_processes, video_info_cache) and threading for background encodes
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "app:app"]
