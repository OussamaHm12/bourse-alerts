from __future__ import annotations

import json
from pathlib import Path


def load_watchlist(path: Path) -> list[str]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    symbols = data.get("symbols", data if isinstance(data, list) else [])
    return [str(symbol).upper() for symbol in symbols]
