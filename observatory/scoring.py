"""Trust (no h-index) and density.

Trust levels come from lists you control (observatory.yaml -> trust, watch_journals,
watch_authors) plus two OpenAlex facts: whether a venue is a repository, and whether an
open-access journal is listed in DOAJ.

Density = sum of the weights of the anchors a work cites
        + term_weight x distinct tracked terms (capped)
        + a small trust bonus.
An anchor's weight falls as its total citation count rises, so being cited alongside a
narrowly read classic counts for more than being cited alongside a famous one.
"""
from __future__ import annotations

from .terms import norm

LEVELS = ["watched", "trusted", "series", "repository", "unknown", "denied"]


def _has(name: str, patterns: list[str]) -> bool:
    n = norm(name or "")
    return bool(n) and any(norm(p) in n for p in patterns if p)


def trust_level(it: dict, profile: dict) -> str:
    t = profile.get("trust", {}) or {}
    venue, publisher = it.get("venue") or "", it.get("publisher") or ""
    if _has(venue, t.get("venue_denylist", [])) or _has(publisher, t.get("publisher_denylist", [])):
        return "denied"
    if it.get("watched") or norm(venue) in {norm(j) for j in profile.get("watch_journals", [])}:
        return "watched"
    if _has(publisher, t.get("trusted_publishers", [])) or _has(venue, t.get("trusted_publishers", [])):
        return "trusted"
    if it.get("venue_doaj"):
        return "trusted"
    if _has(venue, t.get("trusted_series", [])) or _has(publisher, t.get("trusted_series", [])):
        return "series"
    if it.get("venue_type") == "repository":
        return "repository"
    return "unknown"


def anchor_weights(anchors: list[dict], lock: dict, profile: dict) -> dict[str, float]:
    sc = profile.get("scoring", {}) or {}
    ref, exp, floor = sc.get("weight_reference_citations", 150), sc.get("weight_exponent", 0.5), sc.get("weight_floor", 0.08)
    out = {}
    for a in anchors:
        if a.get("weight") is not None:
            out[a["key"]] = float(a["weight"])
            continue
        c = ((lock.get("anchors") or {}).get(a["key"]) or {}).get("cited_by_count") or 0
        out[a["key"]] = round(max(floor, min(1.0, (ref / c) ** exp)) if c else 1.0, 3)
    return out


def density(it: dict, weights: dict[str, float], profile: dict) -> float:
    sc = profile.get("scoring", {}) or {}
    a = sum(weights.get(k, 0.5) for k in it.get("anchors", []))
    t = sc.get("term_weight", 0.25) * min(len(it.get("terms", [])), sc.get("term_cap", 8))
    b = (sc.get("trust_bonus") or {}).get(it.get("trust", "unknown"), 0)
    return round(a + t + b, 3)


def passes_gate(it: dict) -> bool:
    """Citing works always pass (unless denied). Related works need evidence of fit."""
    if it.get("trust") == "denied":
        return False
    if it.get("anchors"):
        return True
    n = len(it.get("terms", []))
    return n >= 2 or (n >= 1 and it.get("trust") == "watched")
