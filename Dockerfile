# NOTE: no `# syntax=docker/dockerfile:1` directive.
#
# That line makes BuildKit fetch the frontend image from docker.io on every build,
# which fails behind a TLS-inspecting proxy and, more importantly, makes the build
# depend on a registry round-trip it does not need. Docker >= 23 supports
# `--mount=type=secret` and multi-stage builds with the built-in frontend, which
# is everything used below.

# ---------------------------------------------------------------------------- #
# Stage 1 — build the Flutter web app from source                              #
# ---------------------------------------------------------------------------- #
#
# WHY THIS STAGE EXISTS
# The image used to COPY a pre-built `webapp_flutter/` straight from the repo, so
# what Railway served was whatever bundle someone last remembered to rebuild by
# hand. It had drifted from the source, and nothing could detect it
# (AUDIT_2026-07-18.md §12). The specific near-miss: the backend gained
# deny-by-default authentication while the committed bundle had no login screen —
# deploying that pair answers 401 to every request with no way to sign in.
#
# Building here makes the served bundle a function of the source, by construction.
# The committed `webapp_flutter/` is kept as a fallback for a plain `docker build`
# without BuildKit, and CI asserts that the two agree.
FROM ghcr.io/cirruslabs/flutter:stable AS flutter-build

WORKDIR /build

# Dependencies first: pubspec changes far less often than lib/, so this layer
# survives most rebuilds.
COPY flutter_app/pubspec.yaml flutter_app/pubspec.lock ./

# Corporate TLS interception (Forcepoint, on the maintainer's network) re-signs
# pub.dev, which a container does not trust. Mounted as a build secret rather than
# baked into a layer: a CA committed to an image is shipped to everyone who pulls
# it. Absent — the normal case for CI and for any machine without such a proxy —
# this is a no-op.
#   docker build --secret id=ca,src=/c/tmp/ca/corporate-ca.crt .
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then \
        cp /run/secrets/ca /usr/local/share/ca-certificates/corporate-ca.crt && \
        update-ca-certificates; \
    fi && \
    flutter pub get

COPY flutter_app/ ./
RUN flutter build web --release

# ---------------------------------------------------------------------------- #
# Stage 2 — the runtime                                                        #
# ---------------------------------------------------------------------------- #
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .

# Same proxy story as the Flutter stage, for PyPI. `update-ca-certificates`
# appends to the system bundle, and PIP_CERT points pip at it — pip does not read
# the OS trust store on its own, so installing the CA without this line fixes
# nothing. Still a no-op when no secret is mounted.
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then \
        cp /run/secrets/ca /usr/local/share/ca-certificates/corporate-ca.crt && \
        update-ca-certificates && \
        export PIP_CERT=/etc/ssl/certs/ca-certificates.crt; \
    fi && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

# The freshly compiled bundle replaces whatever was committed. Deliberately after
# `COPY . .` so it wins: when the two disagree, the one built from THIS commit's
# source is by definition the correct one.
COPY --from=flutter-build /build/build/web ./webapp_flutter

RUN mkdir -p data

EXPOSE 8000

# Port comes from $PORT when the host injects one (Railway/Render/Fly), else 8000.
CMD ["python", "-m", "moroccan_stock_intelligence.cli", "serve", "--host", "0.0.0.0"]
