FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p data

EXPOSE 8000

# Serves the pre-built Flutter web app committed in webapp_flutter/ (see
# flutter_app/ for the source and README for the rebuild command).
# Port comes from $PORT when the host injects one (Railway/Render/Fly), else 8000.
CMD ["python", "-m", "moroccan_stock_intelligence.cli", "serve", "--host", "0.0.0.0"]
