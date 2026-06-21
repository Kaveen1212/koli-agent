FROM python:3.12-slim

# System deps required by cairosvg (SVG→PNG watermark rendering)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libcairo2 \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        libffi-dev \
        shared-mime-info \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer-cached unless pyproject changes)
COPY pyproject.toml requirements.txt ./
RUN pip install --no-cache-dir uv && uv pip install --system -r requirements.txt

# Copy app source
COPY . .

# Create static dirs the app mounts at startup
RUN mkdir -p static/uploads static/generations static/watermark

# Expose FastAPI port
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
