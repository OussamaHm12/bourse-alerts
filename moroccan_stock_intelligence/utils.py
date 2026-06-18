from __future__ import annotations

import re
import unicodedata


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value)
    return " ".join(text.replace("\xa0", " ").split())


def parse_number(value: str | None) -> float | None:
    if value is None:
        return None
    cleaned = normalize_text(value)
    multiplier = 1.0
    suffix_match = re.search(r"\s([KMB])$", cleaned, flags=re.IGNORECASE)
    if suffix_match:
        suffix = suffix_match.group(1).upper()
        multiplier = {"K": 1_000.0, "M": 1_000_000.0, "B": 1_000_000_000.0}[suffix]
        cleaned = cleaned[: suffix_match.start()].strip()
    cleaned = cleaned.replace("%", "").replace("MAD", "").replace("DH", "").strip()
    if cleaned in {"", "-", "--", "NA", "N/A"}:
        return None
    cleaned = cleaned.replace(" ", "")
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", cleaned):
        return None
    return float(cleaned) * multiplier


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def pct_distance(value: float | None, anchor: float | None) -> float | None:
    if value is None or anchor in (None, 0):
        return None
    return (value - anchor) / anchor * 100
