FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    GRADIO_SERVER_NAME=0.0.0.0 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY requirements.txt .

RUN pip install --upgrade pip && \
    pip install -r requirements.txt && \
    playwright install chromium && \
    playwright install-deps chromium

COPY . .

# Ensure the SQLite cache directory exists at runtime
RUN mkdir -p /app/cache

EXPOSE 7860

CMD ["python", "app.py"]
