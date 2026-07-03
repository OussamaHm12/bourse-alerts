FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p data

EXPOSE 8000

# Default: run the web app (API + PWA + in-process scheduler).
# Port comes from $PORT when the host injects one (Railway/Render/Fly), else 8000.
CMD ["python", "-m", "moroccan_stock_intelligence.cli", "serve", "--host", "0.0.0.0"]
