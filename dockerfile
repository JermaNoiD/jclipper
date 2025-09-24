FROM python:3.12-slim

# Install system dependencies including full FFmpeg with encoders
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libavcodec-extra \
    libavformat-dev \
    libavutil-dev \
    libswscale-dev \
    libx264-dev \
    libx265-dev \
    libaom-dev \
    libvpx-dev \
    libopus-dev \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy application code
COPY app/ .

# Copy requirements and install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose port
EXPOSE 5000

# Run the application
CMD ["python", "app.py"]

