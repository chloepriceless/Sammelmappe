FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Tesseract + German lang + Poppler (for PDF rasterisation)
RUN apt-get update && apt-get install -y --no-install-recommends \
      tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng \
      poppler-utils \
      libjpeg62-turbo zlib1g libfreetype6 liblcms2-2 libwebp7 libtiff6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY app/ ./app/
COPY static/ ./static/

# Non-root user
RUN useradd -m -u 1000 sammelmappe && mkdir -p /data && chown -R sammelmappe:sammelmappe /data /app
USER sammelmappe

ENV DATA_DIR=/data \
    HOST=0.0.0.0 \
    PORT=8080
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys;urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=3)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
