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

# Optional corporate CA (see ci/certs/README.md).
#
# A TLS-inspecting proxy re-signs pub.dev with its own CA, which the host trusts
# and a container does not. `ci/certs/` is committed empty and gitignored for
# certificates, so this COPY always succeeds and installs nothing unless someone
# deliberately dropped a .crt there for a local build.
#
# This was a `--mount=type=secret` first, which is the better mechanism — but
# Railway's builder rejects it ("missing a type=cache argument; other mount types
# are not supported"), and a build that only works on a laptop is not a build.
#
# Verification is never disabled: no --insecure, no ignored certificate errors.
COPY ci/certs/ /usr/local/share/ca-certificates/
RUN update-ca-certificates >/dev/null 2>&1 || true

RUN flutter pub get

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

# Same optional-CA story as the Flutter stage, for PyPI. Note PIP_CERT: pip does
# NOT read the OS trust store, so installing a CA without pointing pip at the
# bundle fixes nothing — a detail that costs an hour to rediscover.
#
# `ca-certificates` is not in python:slim, so update-ca-certificates may be
# absent; the `|| true` keeps the normal (no-proxy) path working either way.
COPY ci/certs/ /usr/local/share/ca-certificates/
RUN update-ca-certificates >/dev/null 2>&1 || true

ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The freshly compiled bundle replaces whatever was committed. Deliberately after
# `COPY . .` so it wins: when the two disagree, the one built from THIS commit's
# source is by definition the correct one.
COPY --from=flutter-build /build/build/web ./webapp_flutter

RUN mkdir -p data

EXPOSE 8000

# Port comes from $PORT when the host injects one (Railway/Render/Fly), else 8000.
CMD ["python", "-m", "moroccan_stock_intelligence.cli", "serve", "--host", "0.0.0.0"]
