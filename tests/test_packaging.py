"""Packaging tests: everything the runtime imports must be in requirements.txt.

Written because I shipped Alembic without adding it — the container would have had
`cli migrate` fail at import, in the one place it has to run. The suite passed
throughout, because the dev environment had it installed.

The class of bug is: an import that works locally and is absent from the image. That
includes packages pip happens to install transitively (urllib3 via requests,
cryptography and py_vapid via pywebpush) — importing them directly while relying on
someone else's dependency tree breaks the day that tree changes, at import, on deploy.

Also guarded: the dev tooling must NOT leak back into the runtime file. The Dockerfile
installs requirements.txt wholesale, so anything listed there ships to production.
"""

from __future__ import annotations

import ast
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUNTIME_PACKAGE = ROOT / "moroccan_stock_intelligence"

# Distribution name -> the module name it actually provides.
_MODULE_OF = {
    "beautifulsoup4": "bs4",
    "psycopg[binary]": "psycopg",
    "python-dotenv": "dotenv",
    "py-vapid": "py_vapid",
    "uvicorn[standard]": "uvicorn",
}

# pandas pulls numpy in as a hard dependency and would be meaningless without it, so
# it does not need its own pin.
_PROVIDED_BY_A_PIN = {"numpy"}

# Optional by design: the LLM narrator is opt-in, the SDK is imported lazily inside
# ClaudeSynthesizer, and get_synthesizer falls back to the deterministic template if
# it is missing. Pinning it would ship an SDK nobody uses.
_OPTIONAL = {"anthropic"}


def _requirements(path: pathlib.Path) -> set[str]:
    names = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#")[0].strip()
        if not line or line.startswith("-r"):
            continue
        dist = line.split("==")[0].strip()
        names.add(_MODULE_OF.get(dist, dist.split("[")[0]).lower())
    return names


def _third_party_imports(package: pathlib.Path) -> set[str]:
    found: set[str] = set()
    for path in package.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
    return {
        name
        for name in found
        if name not in sys.stdlib_module_names
        and name != "moroccan_stock_intelligence"
        and not name.startswith("_")
    }


def test_every_runtime_import_is_pinned():
    declared = _requirements(ROOT / "requirements.txt") | _PROVIDED_BY_A_PIN | _OPTIONAL
    missing = sorted(_third_party_imports(RUNTIME_PACKAGE) - declared)
    assert missing == [], (
        f"{missing} are imported by the runtime package but absent from "
        "requirements.txt — they would be missing from the Docker image"
    )


def test_alembic_is_a_runtime_dependency():
    """The specific miss this file exists for.

    `cli migrate` has to run inside the container — DATABASE_URL is a relative path,
    so migrating from a laptop hits the wrong database — and init_db stamps the
    revision on boot. Both need alembic present in the image.
    """
    assert "alembic" in _requirements(ROOT / "requirements.txt")


@pytest.mark.parametrize("module", ["urllib3", "cryptography", "py_vapid"])
def test_directly_imported_transitive_packages_are_pinned(module):
    """pip installs these via requests / pywebpush, but the code imports them
    itself. Relying on someone else's dependency tree for that is a break waiting
    on an unrelated upgrade."""
    assert module in _requirements(ROOT / "requirements.txt")


def test_dev_tooling_does_not_ship_to_production():
    """The Dockerfile installs requirements.txt wholesale."""
    runtime = _requirements(ROOT / "requirements.txt")
    for tool in ["pytest", "ruff", "black", "pre-commit", "httpx", "pytest-cov"]:
        assert tool not in runtime, f"{tool} would be installed in the production image"


def test_dev_requirements_include_the_runtime_ones():
    """`pip install -r requirements-dev.txt` must give a working environment."""
    text = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
    assert "-r requirements.txt" in text


def test_the_optional_llm_sdk_is_not_pinned():
    """Listing it would ship an SDK the default configuration never imports."""
    assert "anthropic" not in _requirements(ROOT / "requirements.txt")


def test_migrations_reach_the_docker_image():
    """`cli migrate` inside the container needs the scripts, not just the library."""
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    for needed in ["migrations", "alembic.ini"]:
        assert needed not in ignored, f"{needed} is excluded from the image"
    assert (ROOT / "migrations" / "versions").is_dir()
