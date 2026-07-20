#!/usr/bin/env bash
# Run a Flutter command against flutter_app/ in a container.
#
# WHY THIS SCRIPT EXISTS
# ----------------------
# The audit's P0 finding was that `webapp_flutter/` (the bundle Railway serves) had
# drifted from `flutter_app/lib/` (the source), and nothing could detect it: the
# rebuild was a command someone had to remember, typed by hand, with no SDK in the
# runtime image. A bundle that silently lags the source is how you ship an app
# whose backend requires a login and whose frontend has no login screen.
#
# So the rebuild is a script, it is what CI runs, and `verify-bundle.sh` fails the
# build when the two disagree.
#
# CORPORATE TLS INTERCEPTION
# --------------------------
# This machine sits behind a Forcepoint proxy that re-signs TLS. The host OS trusts
# its CA; a container does not, so `pub get` fails with "Got TLS error trying to
# find package ... at https://pub.dev".
#
# If CA_BUNDLE (default /c/tmp/ca/corporate-ca.crt) exists it is installed into the
# container's trust store. This does NOT disable verification — no `--insecure`,
# no `badCertificateCallback`. It teaches the container to trust exactly what its
# host already trusts. On a machine with no proxy the file is simply absent and
# everything works unchanged.
#
# Regenerate the bundle with scripts/export-corporate-ca.ps1.
#
# Usage:
#   scripts/flutter-docker.sh analyze --no-fatal-infos
#   scripts/flutter-docker.sh test
#   scripts/flutter-docker.sh build web --release
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="${REPO_ROOT}/flutter_app"
IMAGE="${FLUTTER_IMAGE:-ghcr.io/cirruslabs/flutter:stable}"
CA_BUNDLE="${CA_BUNDLE:-/c/tmp/ca/corporate-ca.crt}"

if [ $# -eq 0 ]; then
  echo "usage: $0 <flutter-args...>" >&2
  exit 64
fi

# Docker Desktop on Windows needs the leading double slash so Git Bash does not
# rewrite the path into a Windows one.
mount_path() {
  case "$(uname -s)" in
    MINGW* | MSYS* | CYGWIN*) printf '/%s' "$1" ;;
    *) printf '%s' "$1" ;;
  esac
}

# Git Bash rewrites any argument that looks like a POSIX path into a Windows one
# (MSYS path conversion), which mangles the CONTAINER side of a -v/-w argument
# just as readily as the host side. A leading double slash suppresses it and is
# harmless on real POSIX shells.
guest_path() {
  case "$(uname -s)" in
    MINGW* | MSYS* | CYGWIN*) printf '/%s' "$1" ;;
    *) printf '%s' "$1" ;;
  esac
}

GUEST_APP="$(guest_path /app)"
GUEST_CA="$(guest_path /ca)"

DOCKER_ARGS=(--rm -v "$(mount_path "${APP_DIR}"):${GUEST_APP}" -w "${GUEST_APP}")

CA_SETUP=":"
if [ -f "${CA_BUNDLE}" ]; then
  CA_DIR="$(dirname "${CA_BUNDLE}")"
  CA_FILE="$(basename "${CA_BUNDLE}")"
  DOCKER_ARGS+=(-v "$(mount_path "${CA_DIR}"):${GUEST_CA}:ro")
  CA_SETUP="cp /ca/${CA_FILE} /usr/local/share/ca-certificates/ && update-ca-certificates >/dev/null 2>&1"
  echo "note: installing corporate CA from ${CA_BUNDLE}" >&2
fi

# pub get and the command run in ONE container: the pub cache lives inside the
# container filesystem, so splitting them across two `docker run` calls resolves
# dependencies into a layer that is then thrown away — which looks exactly like
# a missing package.
exec docker run "${DOCKER_ARGS[@]}" "${IMAGE}" bash -lc "
  set -euo pipefail
  ${CA_SETUP}
  git config --global --add safe.directory /app 2>/dev/null || true
  flutter pub get
  flutter $*
"
