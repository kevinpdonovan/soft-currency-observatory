"""Build the static site from config/ and data/."""
from __future__ import annotations

import datetime as dt
import glob
import html
import json
import shutil
from collections import Counter, defaultdict
from email.utils import format_datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .items import DATA, KIND_LABELS, Corpus, load_json, period_label
from .profile import (ROOT, load_anchors, load_content, load_lexicon, load_lock, load_profile,
                      load_readings, load_site, load_sources)
from .scoring import anchor_weights
from .terms import Vocabulary

SITE = ROOT / "site"
TEMPLATES = ROOT / "templates"
STATIC = ROOT / "static"


def plabel(p: str) -> str:
    return "Founding accessions" if p == "founding" else period_label(p)


def load_accessions() -> list[dict]:
    acc = [load_json(Path(p), {}) for p in glob.glob(str(DATA / "accessions" / "*.json"))]
    acc = [a for a in acc if a.get("period")]
    acc.sort(key=lambda a: a.get("published_at", ""), reverse=True)
    for a in acc:
        a["label"] = plabel(a["period"])
        a["by_kind"] = {k: [i for i in a["items"] if i.get("kind") == k] for k in KIND_LABELS}
    return acc


def _paras(text: str) -> list[str]:
    return [p.strip().replace("\n", " ") for p in (text or "").split("\n\n") if p.strip()]


def density_stats(corpus: Corpus, anchors: list[dict], lock: dict, profile: dict, latest: str | None) -> dict:
    weights = anchor_weights(anchors, lock, profile)
    works = list(corpus.works.values())
    this_year = dt.date.today().year
    years = list(range(this_year - 11, this_year + 1))
    a_count, a_new = Counter(), Counter()
    for w in works:
        for k in w.get("anchors", []):
            a_count[k] += 1
            if latest and w.get("first_seen") == latest:
                a_new[k] += 1
    arows = []
    for a in anchors:
        info = (lock.get("anchors") or {}).get(a["key"]) or {}
        arows.append({"key": a["key"], "cite": a["cite"], "weight": weights.get(a["key"], 0),
                      "corpus": a_count[a["key"]], "new": a_new[a["key"]],
                      "cited_by": info.get("cited_by_count"), "matched": bool(info.get("id")),
                      "check": bool(info.get("check"))})
    arows.sort(key=lambda r: (-r["corpus"], r["cite"]))

    vocab = Vocabulary(profile)
    t_total, t_new, t_year = Counter(), Counter(), defaultdict(Counter)
    for w in works:
        for t in w.get("terms", []):
            t_total[t] += 1
            if w.get("year"):
                t_year[t][int(w["year"])] += 1
            if latest and w.get("first_seen") == latest:
                t_new[t] += 1
    trows = []
    for t in vocab.groups:
        series = [t_year[t].get(y, 0) for y in years]
        recent, earlier = sum(series[-3:]) / 3, (sum(series[:-3]) / max(1, len(series) - 3))
        trows.append({"term": t, "total": t_total[t], "new": t_new[t], "series": series,
                      "trend": round(recent / earlier, 2) if earlier else (None if not recent else 99)})
    trows.sort(key=lambda r: -r["total"])
    status = Counter(w.get("status") for w in works)
    return {"anchors": arows, "terms": trows, "years": years, "n_works": len(works),
            "n_citing": sum(1 for w in works if w.get("anchors")), "status": dict(status),
            "top_new_terms": [t for t, _ in t_new.most_common(14)] or [r["term"] for r in trows[:14] if r["total"]]}


def _rss(site: dict, accs: list[dict]) -> str:
    base = (site.get("base_url") or "").rstrip("/")
    entries = []
    for a in accs[:24]:
        link = f"{base}/accessions/{a['period']}.html"
        lis = "".join(f'<li><a href="{html.escape(i["url"])}">{html.escape(i["title"])}</a>'
                      f'{" — " + html.escape(i["note"]) if i.get("note") else ""}</li>' for i in a["items"][:40])
        pub = dt.datetime.fromisoformat(a["published_at"]).replace(tzinfo=dt.timezone.utc)
        entries.append(f"<item><title>{html.escape(site['title'])}: {html.escape(a['label'])}</title><link>{link}</link>"
                       f"<guid isPermaLink=\"false\">{a['period']}</guid><pubDate>{format_datetime(pub)}</pubDate>"
                       f"<description>{html.escape('<ul>' + lis + '</ul>')}</description></item>")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>'
            f"<title>{html.escape(site['title'])}</title><link>{base}/</link>"
            f"<description>{html.escape(site.get('tagline', ''))}</description>{''.join(entries)}</channel></rss>\n")


def build(log=print) -> None:
    site, profile, content = load_site(), load_profile(), load_content()
    anchors, lock = load_anchors(), load_lock()
    readings, lexicon, sources = load_readings(), load_lexicon(), load_sources()
    accs = load_accessions()
    corpus = Corpus()
    latest = accs[0]["period"] if accs else None
    dens = density_stats(corpus, anchors, lock, profile, latest)
    cite_of = {a["key"]: a["cite"] for a in anchors}

    # lexicon counts
    by_term = {r["term"]: r for r in dens["terms"]}
    for e in lexicon.get("entries", []) or []:
        r = by_term.get(e.get("tracks", ""))
        e["count"] = r["total"] if r else None
        e["series"] = r["series"] if r else []
        e["paras"] = _paras(e.get("text", ""))

    today = dt.date.today().isoformat()
    sessions = content.get("seminar", {}).get("sessions", []) or []
    upcoming = sorted([s for s in sessions if not s.get("date") or s["date"] >= today], key=lambda s: s.get("date") or "9999")
    past = sorted([s for s in sessions if s.get("date") and s["date"] < today], key=lambda s: s["date"], reverse=True)

    lineages = [load_json(Path(p), {}) for p in sorted(glob.glob(str(DATA / "lineage" / "*.json")))]
    health = load_json(DATA / "health.json", {})

    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html", "xml"]),
                      trim_blocks=True, lstrip_blocks=True)
    env.filters["nicedate"] = lambda d: dt.date.fromisoformat(d).strftime("%-d %B %Y") if d else ""
    env.filters["paras"] = _paras
    env.filters["monthyear"] = lambda d: dt.date.fromisoformat(d).strftime("%b %Y") if d else ""
    if SITE.exists():
        shutil.rmtree(SITE)
    (SITE / "accessions").mkdir(parents=True)
    (SITE / "lineage").mkdir()
    shutil.copytree(STATIC, SITE / "static")
    common = dict(site=site, content=content, profile=profile, kinds=KIND_LABELS, accessions=accs, cite_of=cite_of,
                  dens=dens, built=dt.datetime.utcnow().strftime("%-d %b %Y"), lineages=lineages,
                  proxies_json=json.dumps(site.get("library_proxies", [])), n_anchors=len(anchors))

    def render(tpl, out, root="", **ctx):
        (SITE / out).write_text(env.get_template(tpl).render(**common, root=root, **ctx), encoding="utf-8")

    render("index.html", "index.html", page="home", latest=accs[0] if accs else None, upcoming=upcoming[:1])
    render("research.html", "research.html", page="research")
    render("seminar.html", "seminar.html", page="seminar", upcoming=upcoming, past=past)
    render("archive.html", "archive.html", page="archive")
    for a in accs:
        render("accession.html", f"accessions/{a['period']}.html", root="../", page="archive", acc=a)
    render("readings.html", "readings.html", page="readings", readings=readings)
    render("lexicon.html", "lexicon.html", page="lexicon", lexicon=lexicon)
    render("density.html", "density.html", page="density", anchors=anchors)
    render("join.html", "join.html", page="join")
    render("about.html", "about.html", page="about", sources=sources, health=health, anchors=anchors)
    for L in lineages:
        render("lineage.html", f"lineage/{L['key']}.html", root="../", page="density", L=L)
    (SITE / "feed.xml").write_text(_rss(site, accs), encoding="utf-8")
    (SITE / "items.json").write_text(json.dumps(
        [{k: i.get(k) for k in ("id", "kind", "title", "url", "venue", "source", "date", "year", "authors", "anchors",
                                "terms", "note")} | {"period": a["period"], "period_label": a["label"]}
         for a in accs for i in a["items"]], ensure_ascii=False), encoding="utf-8")
    (SITE / "anchors.json").write_text(json.dumps(cite_of, ensure_ascii=False), encoding="utf-8")
    (SITE / "robots.txt").write_text("User-agent: *\nAllow: /\n" if site.get("public") else "User-agent: *\nDisallow: /\n")
    (SITE / ".nojekyll").write_text("")
    log(f"built site: {len(accs)} accession(s), {dens['n_works']} works in corpus -> {SITE}")
