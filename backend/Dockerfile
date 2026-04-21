# Backend image for pocketwatchai.
#
# Includes the native Tesseract OCR binary so scanned PDFs can be parsed
# server-side. Without this, `pytesseract` has nothing to call and scanned
# uploads fail with a clear "install Tesseract" error.
#
# Build locally:  docker build -t pocketwatch-backend -f backend/Dockerfile .
# Run locally:    docker run -p 8000:8000 -e OPENAI_API_KEY=sk-... pocketwatch-backend
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TESSERACT_CMD=/usr/bin/tesseract

# System deps:
#   tesseract-ocr        - OCR engine used by pytesseract
#   tesseract-ocr-eng    - English language data (default)
#   poppler-utils        - not strictly required (pypdfium2 handles rendering)
#                          but useful if a future backend dependency needs it
#   libjpeg / zlib       - Pillow runtime deps
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      tesseract-ocr \
      tesseract-ocr-eng \
      libjpeg62-turbo \
      zlib1g \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

COPY backend/ /app/

EXPOSE 8000

# Render / Fly set $PORT. Fall back to 8000 for local `docker run`.
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
