"""The monthly review lives in a GitHub issue with tick-boxes.

harvest  -> data/runs/<period>/candidates.json and review.md (the issue body)
reviewer -> unticks what shouldn't be published, adds ★ to feature, ticks candidate anchors
            to promote, closes the issue
publish  -> reads the closed issue and publishes the ticked works
"""
from __future__ import annotations

import re

LINE_RX = re.compile(r"^\s*[-*]\s*\[(?P<tick>[ xX])\]\s*(?P<star>★|⭐)?.*?<!--(?P<kind>id|cand):(?P<id>[\w-]+)-->", re.M)


def ai(it: dict) -> dict:
    return it.get("ai") or {}


def rank_key(it: dict):
    return (ai(it).get("relevance", 2), it.get("density", 0), ai(it).get("quality", 1), it.get("date", ""))


def pretick(it: dict, profile: dict) -> bool:
    thr = (profile.get("scoring") or {}).get("pretick_density", 1.0)
    a = ai(it)
    if a and (a.get("relevance", 0) < 2 or a.get("quality", 1) < 1):
        return False
    if it.get("trust") == "unknown" and len(it.get("anchors", [])) < 2:
        return False
    if it["kind"] == "citing":
        return it.get("density", 0) >= thr or a.get("relevance", 0) >= 3
    return a.get("relevance", 0) >= 3 or (not a and it.get("density", 0) >= thr + 0.5)


def select(items: list[dict], profile: dict) -> dict[str, list[dict]]:
    cap = (profile.get("scoring") or {}).get("review_max", {})
    out = {}
    for kind in ("citing", "related"):
        pool = [i for i in items if i["kind"] == kind and ai(i).get("relevance", 1) >= 1]
        pool.sort(key=rank_key, reverse=True)
        out[kind] = pool[: cap.get(kind, 40)]
    return out


def _esc(s: str) -> str:
    return (s or "").replace("[", "(").replace("]", ")").replace("\n", " ")


def _line(it: dict, tick: bool, cites: dict[str, str]) -> str:
    anchors = ", ".join(cites.get(k, k) for k in it.get("anchors", [])[:4])
    extra = f" +{len(it['anchors']) - 4}" if len(it.get("anchors", [])) > 4 else ""
    by = ", ".join(it.get("authors", [])[:2]) + (" et al." if len(it.get("authors", [])) > 2 else "")
    meta = " · ".join(x for x in [by, it.get("venue") or it.get("source", ""), str(it.get("year") or it.get("date", "")[:4]),
                                  f"trust: {it.get('trust')}" if it.get("trust") in ("unknown", "repository") else ""] if x)
    parts = [f"- [{'x' if tick else ' '}] [{_esc(it['title'])[:200]}]({it['url']}) · {meta}",
             f"`d{it.get('density', 0):.2f}`"]
    if anchors:
        parts.append(f"cites: {anchors}{extra}")
    if it.get("terms"):
        parts.append("terms: " + ", ".join(it["terms"][:5]))
    if ai(it).get("note"):
        parts.append(f"— {ai(it)['note']}")
    return " · ".join(parts) + f" <!--id:{it['id']}-->"


def issue_body(label: str, period: str, chosen: dict, candidates: list[dict], stats: dict,
               profile: dict, cites: dict[str, str], backlog: int = 0) -> str:
    L = [f"<!-- observatory:period={period} -->",
         f"**{label}.** {stats.get('harvested', 0)} records harvested; {stats.get('duplicates', 0)} duplicates and "
         f"{stats.get('already_seen', 0)} works already in the corpus set aside; {stats.get('new', 0)} new works entered "
         f"the corpus ({stats.get('citing', 0)} citing an anchor). The strongest are listed below, ranked by relevance "
         f"and density (`d`)."
         + (f" {backlog} further works are in the corpus and count towards density but are not listed." if backlog else ""),
         "",
         "**How to review:** untick anything that shouldn't enter the archive. Add ★ after a box to feature a work. "
         "Tick a *candidate anchor* to add it to the anchor list (and the archive). Then **close the issue**; the site "
         "updates within minutes. Unticked works stay in the corpus but are not shown again.",
         ""]
    for kind, head in (("citing", "Citing the anchors"), ("related", "Related work")):
        items = chosen.get(kind, [])
        L.append(f"### {head} ({len(items)})")
        L += [_line(it, pretick(it, profile), cites) for it in items] or ["_Nothing this month._"]
        L.append("")
    L.append(f"### Candidate anchors ({len(candidates)})")
    L.append("_Older works cited by many works in the corpus that are not yet anchors. Tick to promote._")
    for c in candidates:
        by = ", ".join(c.get("authors", [])[:2])
        L.append(f"- [ ] [{_esc(c['title'])[:200]}]({c['url']}) · {by} · {c.get('year', '')} · "
                 f"cited by {c.get('cocited_by', 0)} corpus works · {c.get('cited_by_count', 0)} citations overall"
                 f" <!--cand:{c['openalex_id']}-->")
    return "\n".join(L)[:65000]


def parse_review(body: str) -> tuple[list[str], list[str], list[str]]:
    approved, featured, promote = [], [], []
    for m in LINE_RX.finditer(body or ""):
        if m.group("tick").lower() != "x":
            continue
        if m.group("kind") == "cand":
            promote.append(m.group("id"))
        else:
            approved.append(m.group("id"))
            if m.group("star"):
                featured.append(m.group("id"))
    return approved, featured, promote


def period_from_body(body: str) -> str | None:
    m = re.search(r"observatory:period=([\w-]+)", body or "")
    return m.group(1) if m else None


def parse_form(body: str) -> dict:
    out, cur = {}, None
    for line in (body or "").splitlines():
        if line.startswith("### "):
            cur = line[4:].strip()
            out[cur] = []
        elif cur is not None:
            out[cur].append(line)
    return {k: ("" if "\n".join(v).strip() in ("_No response_", "None") else "\n".join(v).strip()) for k, v in out.items()}
