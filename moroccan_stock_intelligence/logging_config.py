from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime


def _utc_converter(*_args):
    return datetime.now(UTC).timetuple()


def configure_logging(level: str = "INFO") -> None:
    logging.Formatter.converter = _utc_converter
    logging.basicConfig(
        level=level.upper(),
        format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
        datefmt="%Y-%m-%dT%H:%M:%SZ",
        stream=sys.stdout,
        force=True,
    )
