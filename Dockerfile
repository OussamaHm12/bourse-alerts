# ---- Stage 1: compile the Flutter web app ----
FROM ghcr.io/cirruslabs/flutter:stable AS webbuild
WORKDIR /flutter_app
COPY flutter_app/ ./
# On a corporate build network with TLS interception, pass the host CA bundle as a
# BuildKit secret named "ca". On open networks (e.g. Railway) no secret is needed
# and this trust step is skipped.
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then cat /run/secrets/ca >> /etc/ssl/certs/ca-certificates.crt; fi; \
    export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt; \
    git config --global --add safe.directory '*'; \
    flutter pub get && flutter build web --release --pwa-strategy=none

# ---- Stage 2: Python API + PWA + in-process scheduler ----
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
# The "ca" secret is only present on a corporate build network (TLS interception);
# it makes pip trust pypi.org. On open networks (Railway) it is absent and pip uses
# its default certifi bundle.
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then \
      cat /run/secrets/ca >> /etc/ssl/certs/ca-certificates.crt; \
      export PIP_CERT=/etc/ssl/certs/ca-certificates.crt; \
    fi; \
    pip install --no-cache-dir -r requirements.txt

COPY . .
# The compiled Flutter web app; FastAPI serves this (webapp_flutter) when present.
COPY --from=webbuild /flutter_app/build/web ./webapp_flutter
RUN mkdir -p data

EXPOSE 8000

# Port comes from $PORT when the host injects one (Railway/Render/Fly), else 8000.
CMD ["python", "-m", "moroccan_stock_intelligence.cli", "serve", "--host", "0.0.0.0"]
