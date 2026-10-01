"""Item records, periods, JSON helpers, and the cumulative corpus."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit, urlunsplit

from .profile import DATA

KINDS = ["citing", "related", "older"]
KIND_LABELS = {"citing": "Citing the anchors", "related": "Related work", "older": "From the archive"}
_TRACKING = re.compile(r"^(utm_|fbclid|gclid|mc_cid|mc_eid|cmpid|ref$|src$)")


def clean_url(url: str) -> str:
    if not url:
        return ""
    p = urlsplit(url.strip())
    q = [(k, v) for k, v in parse_qsl(p.query) if not _TRACKING.match(k)]
    return urlunsplit((p.scheme or "https", p.netloc.lower(), p.path.rstrip("/") or "/", urlencode(q), ""))


def item_id(url: str = "", doi: str = "", title: str = "") -> str:
    key = (doi or "").lower().strip() or clean_url(url) or re.sub(r"\W+", "", (title or "").lower())
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]


def make_item(*, kind, title, url, source="", date="", doi="", authors=None, summary="",
              venue="", origin="", extra=None) -> dict:
    title = re.sub(r"\s+", " ", title or "").strip()
    doi = (doi or "").replace("https://doi.org/", "").strip().lower()
    return {
        "id": item_id(url, doi, title),
        "kind": kind,
        "title": title,
        "url": f"https://doi.org/{doi}" if doi else (clean_url(url) or
                                                      f"https://scholar.google.com/scholar?q={quote_plus(title)}"),
        "doi": doi,
        "source": source,
        "venue": venue,
        "authors": authors or [],
        "date": (date or "")[:10],
        "summary": re.sub(r"<[^>]+>", " ", summary or "")[:2000].strip(),   # scoring only; never published
        "origin": origin,
        "anchors": [],          # keys of anchors this work cites
        **(extra or {}),
    }


def period(d: dt.date | None = None) -> str:
    d = d or dt.date.today()
    return f"{d.year}-{d.month:02d}"


def period_label(p: str) -> str:
    y, m = p.split("-")[:2]
    return dt.date(int(y), int(m), 1).strftime("%B %Y")


def load_json(path: Path, default):
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


# ------------------------------------------------------------------ corpus
PUBLIC_FIELDS = ("id", "openalex_id", "doi", "title", "authors", "year", "date", "venue", "url",
                 "anchors", "terms", "density", "trust", "first_seen", "status", "origin", "work_type")


class Corpus:
    """Every work the Observatory has ever seen, one JSON object per line.

    data/corpus/works.jsonl   metadata, anchors cited, terms found, density, status
    data/corpus/refs.jsonl    {"id": ..., "refs": [OpenAlex ids]}  (for co-citation)

    status: new (awaiting review) | published | passed (reviewed, not published) | backlog
    Line-per-record JSON keeps GitHub diffs readable.
    """

    def __init__(self, root: Path = DATA / "corpus"):
        self.root = root
        self.works: dict[str, dict] = {}
        self.refs: dict[str, list[str]] = {}
        self.keys: dict[str, str] = {}      # fuzzy work_key -> id
        self.oa: dict[str, str] = {}        # OpenAlex id -> id
        for line in self._lines("works.jsonl"):
            w = json.loads(line)
            self.works[w["id"]] = w
            if w.get("work_key"):
                self.keys[w["work_key"]] = w["id"]
            if w.get("openalex_id"):
                self.oa[w["openalex_id"]] = w["id"]
        for line in self._lines("refs.jsonl"):
            r = json.loads(line)
            self.refs[r["id"]] = r["refs"]

    def _lines(self, name):
        p = self.root / name
        return [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()] if p.exists() else []

    def find(self, it: dict) -> dict | None:
        if it["id"] in self.works:
            return self.works[it["id"]]
        if it.get("openalex_id") and it["openalex_id"] in self.oa:
            return self.works.get(self.oa[it["openalex_id"]])
        if it.get("work_key") and it["work_key"] in self.keys:
            return self.works.get(self.keys[it["work_key"]])
        return None

    def add(self, it: dict, status: str, when: str) -> dict:
        rec = {k: it.get(k) for k in PUBLIC_FIELDS if it.get(k) not in (None, "", [])}
        rec["work_key"] = it.get("work_key", "")
        rec["status"] = status
        rec["first_seen"] = when
        self.works[it["id"]] = rec
        if it.get("work_key"):
            self.keys[it["work_key"]] = it["id"]
        if it.get("openalex_id"):
            self.oa[it["openalex_id"]] = it["id"]
        if it.get("referenced_works"):
            self.refs[it["id"]] = [r.split("/")[-1] for r in it["referenced_works"]]
        return rec

    def merge_anchors(self, rec: dict, anchors: list[str]) -> None:
        rec["anchors"] = sorted(set(rec.get("anchors", [])) | set(anchors))

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        ws = sorted(self.works.values(), key=lambda w: (w.get("first_seen", ""), w["id"]))
        (self.root / "works.jsonl").write_text(
            "".join(json.dumps(w, ensure_ascii=False, sort_keys=True) + "\n" for w in ws), encoding="utf-8")
        (self.root / "refs.jsonl").write_text(
            "".join(json.dumps({"id": k, "refs": v}) + "\n" for k, v in sorted(self.refs.items())), encoding="utf-8")
