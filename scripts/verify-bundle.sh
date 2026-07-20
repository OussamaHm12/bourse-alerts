#!/usr/bin/env bash
# Fail if the committed Flutter bundle no longer matches its source.
#
# THE BUG THIS MAKES IMPOSSIBLE
# -----------------------------
# `webapp_flutter/` is what Railway serves; `flutter_app/lib/` is what the source
# says. The rebuild was a manual command with no SDK in the runtime image, so the
# two could drift with nothing to detect it — and they had (AUDIT_2026-07-18.md
# §12). The concrete consequence: the backend gained deny-by-default
# authentication while the served bundle contained no login screen, so shipping
# that pair would have answered 401 to every request with no way for the owner to
# sign in.
#
# A stale bundle is not a style problem. It is a deployment that does something
# other than what the code under review says.
#
# WHAT IT CHECKS
#   1. the bundle exists and carries the expected entrypoints
#   2. rebuilding from source produces a functionally identical main.dart.js
#   3. the auth flow is present in the shipped artefact
#
# (2) compares a normalised hash. Dart2js output is deterministic for identical
# input, but the build stamps a timestamp into version.json and flutter_bootstrap
# carries a build id, so those two files are excluded from the comparison rather
# than making every run report a false difference.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUNDLE="${REPO_ROOT}/webapp_flutter"
BUILD_OUTPUT="${REPO_ROOT}/flutter_app/build/web"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

# ---- 1. the bundle is there and is a Flutter build ------------------------- #
[ -d "${BUNDLE}" ] || fail "${BUNDLE} does not exist"
for required in main.dart.js index.html flutter_bootstrap.js manifest.json; do
  [ -f "${BUNDLE}/${required}" ] || fail "${BUNDLE}/${required} is missing"
done

# ---- 2. the shipped bundle can actually authenticate ----------------------- #
# Asserted on the artefact, not on the source: the whole point is that the source
# can be right while the thing being served is not.
grep -q "auth/login" "${BUNDLE}/main.dart.js" \
  || fail "the bundle contains no login flow — it predates the auth layer and would 401 on every route"
grep -q "auth/status" "${BUNDLE}/main.dart.js" \
  || fail "the bundle cannot probe an existing session"

echo "ok: bundle present and carries the auth flow"

# ---- 3. rebuild and compare ------------------------------------------------ #
# Skippable because a rebuild needs Docker and ~2 minutes; CI runs it, a local
# pre-commit check may not want to.
if [ "${SKIP_REBUILD:-0}" = "1" ]; then
  echo "note: SKIP_REBUILD=1, not comparing against a fresh build"
  exit 0
fi

echo "rebuilding from source to compare..."
"${REPO_ROOT}/scripts/flutter-docker.sh" build web --release >/dev/null 2>&1 \
  || fail "the Flutter build failed — the source does not compile"

built_hash="$(sha256sum "${BUILD_OUTPUT}/main.dart.js" | cut -d' ' -f1)"
shipped_hash="$(sha256sum "${BUNDLE}/main.dart.js" | cut -d' ' -f1)"

if [ "${built_hash}" != "${shipped_hash}" ]; then
  cat >&2 <<EOF
FAIL: webapp_flutter/ is stale.

  committed : ${shipped_hash}
  rebuilt   : ${built_hash}

The served bundle differs from what flutter_app/lib/ compiles to, so the deployed
app is not the app in this diff. Refresh it:

    scripts/flutter-docker.sh build web --release
    rm -rf webapp_flutter && cp -r flutter_app/build/web webapp_flutter
    git add webapp_flutter
EOF
  exit 1
fi

echo "ok: committed bundle matches a fresh build of the source"
