FROM python:3.12-slim

# Tesseract OCR with English + Spanish language packs.
# Adds ~150 MB but is required for scanned PDFs and phone-photo invoices.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-spa \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first so source-only edits don't bust the layer cache.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source last — invalidates only the final layer on code changes.
COPY . .

# Drop privileges. Streamlit doesn't need root and the container image is
# inherited by the K8s Deployment, so non-root + a writable HOME is enough.
RUN useradd -m -u 1000 streamlit && chown -R streamlit /app
USER streamlit

EXPOSE 8501

# Python-based healthcheck so we don't have to apt-install curl just for this.
# K8s liveness/readiness probes against /_stcore/health are the real check;
# this is a fallback for `docker run` without an orchestrator.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request, sys; \
sys.exit(0 if urllib.request.urlopen('http://localhost:8501/_stcore/health', timeout=3).read() else 1)" \
    || exit 1

CMD ["streamlit", "run", "app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--server.fileWatcherType=none", \
     "--browser.gatherUsageStats=false"]
