"""Load configuration: profile, anchors (+ lock), sources, site text."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

ROOT = Path(os.environ.get("SCO_ROOT") or Path(__file__).resolve().parent.parent)
CONFIG = ROOT / "config"
DATA = ROOT / "data"


def read_yaml(path: Path, default=None):
    if not path.exists():
        return {} if default is None else default
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or ({} if default is None else default)


def write_yaml(path: Path, obj, header: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        if header:
            f.write(header)
        yaml.safe_dump(obj, f, allow_unicode=True, sort_keys=False, width=110)


def load_profile() -> dict:
    return read_yaml(CONFIG / "observatory.yaml")


def load_sources() -> dict:
    return read_yaml(CONFIG / "sources.yaml")


def load_site() -> dict:
    return read_yaml(CONFIG / "site.yaml")


def load_content() -> dict:
    return read_yaml(CONFIG / "content.yaml")


def load_readings() -> dict:
    return read_yaml(CONFIG / "readings.yaml")


def load_lexicon() -> dict:
    return read_yaml(CONFIG / "lexicon.yaml")


def load_anchors() -> list[dict]:
    return read_yaml(CONFIG / "anchors.yaml").get("anchors", []) or []


LOCK_HEADER = (
    "# What each anchor, watched author and watched journal matched in OpenAlex.\n"
    "# Written automatically on the first run. Check entries marked check: true.\n"
    "# To fix a wrong match, replace the id by hand (search https://openalex.org)\n"
    "# and set manual: true so it is never overwritten; set id: null to skip.\n"
)


def load_lock() -> dict:
    lock = read_yaml(CONFIG / "anchors_lock.yaml")
    for k in ("anchors", "authors", "journals"):
        lock.setdefault(k, {})
    return lock


def save_lock(lock: dict) -> None:
    write_yaml(CONFIG / "anchors_lock.yaml", lock, LOCK_HEADER)


def env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v else default
