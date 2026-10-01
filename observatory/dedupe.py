"""Duplicate detection beyond exact DOI/URL matches.

Scholarship: the same work often appears as a preprint, a repository copy, an
OpenAlex duplicate record and the journal version, or with a punctuation or
capitalisation variant of its title. These share a *work key*
(normalised title + first author surname + year). The best version is kept
and the others are recorded under "also_at".

News: syndicated copies of one story carry near-identical headlines. These
are clustered by word overlap, and the copy from the highest-ranked outlet is
kept.
"""
from __future__ import annotations

import re

from .terms import norm

STOP = {"the", "a", "an", "of", "and", "in", "on", "for", "to", "le", "la", "les", "de", "des", "du", "et",
        "el", "los", "las", "y", "o", "da", "do", "das", "dos", "e"}


def title_tokens(title: str) -> list[str]:
    t = re.sub(r"[^\w\s]", " ", norm(title))
    return [w for w in t.split() if w not in STOP]


def title_key(title: str, n: int = 12) -> str:
    return " ".join(title_tokens(title)[:n])


def surname(authors: list[str]) -> str:
    if not authors:
        return ""
    parts = re.sub(r"[^\w\s-]", " ", norm(authors[0])).split()
    return parts[-1] if parts else ""


def work_key(it: dict) -> str:
    """Fuzzy identity of a scholarly work. Year is left out: preprint and journal years differ."""
    tk = title_key(it.get("title", ""))
    if len(tk.split()) < 3:          # too short to trust
        return ""
    return f"{tk}|{surname(it.get('authors', []))}"


def news_key(it: dict) -> str:
    return "news|" + title_key(it.get("title", ""), 10)


# ------------------------------------------------------------- scholarship
VERSION_RANK = {  # higher is better
    "journal": 5, "book series": 4, "ebook platform": 4, "conference": 3,
    "repository": 1, "": 0,
}
TYPE_RANK = {"article": 3, "book": 3, "book-chapter": 3, "review": 2, "report": 2, "preprint": 1}


def version_score(it: dict) -> tuple:
    return (
        VERSION_RANK.get(it.get("venue_type", ""), 2),
        TYPE_RANK.get(it.get("work_type", ""), 1),
        1 if it.get("doi") else 0,
        len(it.get("summary", "")),
    )


def merge_versions(items: list[dict]) -> tuple[list[dict], int]:
    """Collapse scholarly items that share a work key. Returns (items, n_removed)."""
    groups: dict[str, list[dict]] = {}
    out, removed = [], 0
    for it in items:
        if it.get("kind") not in ("citing", "related", "older") or not it.get("work_key"):
            out.append(it)
            continue
        groups.setdefault(it["work_key"], []).append(it)
    for grp in groups.values():
        grp.sort(key=version_score, reverse=True)
        best = grp[0]
        others = grp[1:]
        if others:
            removed += len(others)
            best["also_at"] = sorted({o.get("source") or o.get("url") for o in others} - {best.get("source")})
            # keep the richest abstract for tagging, and the union of flags
            best["summary"] = max((g.get("summary", "") for g in grp), key=len)
            best["watched"] = any(g.get("watched") for g in grp)
            best["anchors"] = sorted({a for g in grp for a in g.get("anchors", [])})
            best["referenced_works"] = max((g.get("referenced_works", []) for g in grp), key=len)
            best["origins"] = sorted({g.get("origin", "") for g in grp})
        out.append(best)
    return out, removed


# ------------------------------------------------------------- news
def _jaccard(a: set, b: set) -> float:
    return len(a & b) / max(1, len(a | b))


def cluster_news(items: list[dict], outlet_rank: dict[str, int], threshold: float = 0.6) -> tuple[list[dict], int]:
    """Keep one copy of each syndicated story; record the other outlets."""
    news = [i for i in items if i.get("section") == "news"]
    rest = [i for i in items if i.get("section") != "news"]
    clusters: list[list[dict]] = []
    toks = {}
    for it in news:
        t = set(title_tokens(it.get("title", "")))
        toks[it["id"]] = t
        for c in clusters:
            if _jaccard(t, toks[c[0]["id"]]) >= threshold and len(t) >= 4:
                c.append(it)
                break
        else:
            clusters.append([it])
    kept, removed = [], 0
    worst = 10_000
    for c in clusters:
        c.sort(key=lambda i: (outlet_rank.get(domain_of(i), worst), -len(i.get("summary", ""))))
        best = c[0]
        if len(c) > 1:
            removed += len(c) - 1
            best["also_at"] = sorted({domain_of(o) or o.get("source", "") for o in c[1:]} - {domain_of(best)})
        kept.append(best)
    return rest + kept, removed


def domain_of(it: dict) -> str:
    m = re.match(r"https?://(?:www\.)?([^/]+)", it.get("url", ""))
    return m.group(1).lower() if m else ""


def keys_for(it: dict) -> list[str]:
    """All the keys under which an item is remembered across weeks."""
    ks = [it["id"]]
    if it.get("section") == "news":
        k = news_key(it)
        if len(k) > 20:
            ks.append(k)
    elif it.get("work_key"):
        ks.append(it["work_key"])
    return ks
