FROM python:3.12-slim
# Set WITH_OCR=true at build time if you want the offline Tesseract backend.
ARG WITH_OCR=false
RUN if [ "$WITH_OCR" = "true" ]; then \
      apt-get update && apt-get install -y --no-install-recommends tesseract-ocr \
      && rm -rf /var/lib/apt/lists/*; fi
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8080
CMD ["gunicorn", "-b", "0.0.0.0:8080", "-w", "2", "--timeout", "90", "app.server:app"]
