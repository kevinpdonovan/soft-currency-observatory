"""Command line.

  python -m observatory resolve              # match anchors / authors / journals in OpenAlex
  python -m observatory backfill             # first run: all existing citing works -> corpus + founding review
  python -m observatory harvest              # monthly sweep -> corpus + review issue body
  python -m observatory publish BODY.md      # publish ticked works from a closed review issue
  python -m observatory build                # rebuild the site
  python -m observatory terms                # suggest tracked terms from the anchor literature
  python -m observatory lineage KEY|WID      # citation lineage of one work (side project)
  python -m observatory suggest FORM.md USER # add a hand-suggested work (issue form)
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from collections import Counter
from pathlib import Path

import yaml

from . import build as site
from . import openalex as oa
from .dedupe import merge_versions, work_key
from .items import DATA, Corpus, load_json, make_item, period, save_json
from .profile import CONFIG, load_anchors, load_lock, load_profile, load_sources, save_lock, write_yaml
from .review import issue_body, parse_form, parse_review, period_from_body, select
from .scholarly_extra import email_alerts, s2_citing
from .scoring import anchor_weights, density, passes_gate, trust_level
from .tagger import tag_items
from .terms import Vocabulary, suggest_terms


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def _health(name, ok, n, note=""):
    return {"source": name, "ok": ok, "count": n, "note": (note or "")[:200]}


# ------------------------------------------------------------------ resolve
def cmd_resolve(args=None):
    anchors, lock, profile = load_anchors(), load_lock(), load_profile()
    oa.resolve_anchors(anchors, lock, log)
    oa._resolve_names("authors", profile.get("watch_authors", []), lock)
    oa._resolve_names("journals", profile.get("watch_journals", []), lock)
    save_lock(lock)
    return lock


# ------------------------------------------------------------------ collect
def _gather(backfill: bool, since_email: dt.date):
    profile, sources, anchors = load_profile(), load_sources(), load_anchors()
    lock = cmd_resolve()
    ids = oa.anchor_ids(lock)
    raw, health = [], []
    for src in sources.get("citing") or []:
        t, name = src.get("type"), src.get("name", "?")
        try:
            if t == "openalex_citing":
                since = None if backfill else dt.date.today() - dt.timedelta(days=30 * src.get("lookback_months", 18))
                got = oa.citing(ids, since, cap=src.get("backfill_cap_per_anchor", 3000) if backfill else 3000,
                                per_anchor=backfill, log=log)
                h = _health(name, True, len(got))
            elif t == "s2_citing":
                since = None if backfill else dt.date.today() - dt.timedelta(days=30 * src.get("lookback_months", 18))
                got = s2_citing(anchors, lock, since, cap=src.get("backfill_cap_per_anchor", 1000) if backfill else 300, log=log)
                h = _health(name, True, len(got))
            elif t == "email" and not backfill:
                got, note = email_alerts(src, anchors, since_email, log)
                h = _health(name, True, len(got), note)
            else:
                continue
        except Exception as ex:
            got, h = [], _health(name, False, 0, str(ex))
        log(f"· citing   {name}: {h['count']}{'' if h['ok'] else '  (FAILED)'}")
        raw += got
        health.append(h)
    if not backfill:
        for src in sources.get("related") or []:
            try:
                got = oa.related(src, profile, lock, ids, log)
                h = _health(src["name"], True, len(got))
            except Exception as ex:
                got, h = [], _health(src["name"], False, 0, str(ex))
            log(f"· related  {src['name']}: {h['count']}{'' if h['ok'] else '  (FAILED)'}")
            raw += got
            health.append(h)
    save_lock(lock)
    return profile, sources, anchors, lock, ids, raw, health


def _enrich(raw: list[dict], ids: dict[str, str], log) -> None:
    """Give Semantic Scholar / Scholar-alert records OpenAlex metadata where possible
    (references for co-citation, venue for trust, abstract for terms)."""
    need = [it for it in raw if not it.get("openalex_id")]
    by_doi = oa.works_by_doi([it["doi"] for it in need if it.get("doi")])
    looked = 0
    for it in need:
        w = by_doi.get(it.get("doi", ""))
        if not w and it.get("origin") == "email:scholar" and looked < 80:
            w = oa.find_by_title(it["title"])
            looked += 1
        if w:
            anchors = set(it.get("anchors", []))
            rich = oa.oa_to_item(w, it["kind"], it["origin"])
            rich["anchors"] = sorted(anchors | {ids[r] for r in rich.get("referenced_works", []) if r in ids})
            it.clear()
            it.update(rich)


def _process(profile, anchors, lock, raw, corpus: Corpus, when: str, log):
    vocab = Vocabulary(profile)
    weights = anchor_weights(anchors, lock, profile)
    stats = Counter(harvested=len(raw))
    uniq = {}
    for it in raw:
        if not it.get("title") or vocab.negative_hit(it["title"]):
            stats["dropped_negative"] += 1
            continue
        it["work_key"] = work_key(it)
        if it["id"] in uniq:
            prev = uniq[it["id"]]
            prev["anchors"] = sorted(set(prev.get("anchors", [])) | set(it.get("anchors", [])))
            if len(it.get("summary", "")) > len(prev.get("summary", "")):
                it["anchors"] = prev["anchors"]
                uniq[it["id"]] = it
            continue
        uniq[it["id"]] = it
    items, n_versions = merge_versions(list(uniq.values()))
    stats["duplicates"] = (len(raw) - stats["dropped_negative"] - len(uniq)) + n_versions

    fresh = []
    for it in items:
        if it.get("anchors") and it["kind"] == "related":
            it["kind"] = "citing"
        it["terms"] = vocab.find(it["title"], it.get("summary", ""))
        it["trust"] = trust_level(it, profile)
        it["density"] = density(it, weights, profile)
        rec = corpus.find(it)
        if rec:   # already known: record any newly discovered anchor links, never re-review
            stats["already_seen"] += 1
            before = set(rec.get("anchors", []))
            corpus.merge_anchors(rec, it.get("anchors", []))
            if set(rec["anchors"]) != before:
                rec["density"] = density({**rec, "terms": rec.get("terms", [])}, weights, profile)
            continue
        if not passes_gate(it):
            stats["failed_gate"] += 1
            continue
        fresh.append(it)
    stats["new"] = len(fresh)
    stats["citing"] = sum(1 for i in fresh if i["kind"] == "citing")
    return fresh, stats, weights


def _cocitation(corpus: Corpus, ids: dict[str, str], src: dict, log) -> list[dict]:
    rejected = set(load_json(DATA / "candidates_seen.json", []))
    counts = Counter()
    for wid, refs in corpus.refs.items():
        w = corpus.works.get(wid, {})
        if w.get("status") in ("published", "new", "backlog") and (w.get("anchors") or w.get("density", 0) >= 0.75):
            counts.update(set(refs))
    pool = [(r, n) for r, n in counts.most_common(200)
            if r not in ids and r not in rejected and n >= src.get("min_citing_works", 4)
            and r not in corpus.oa][: src.get("max_items", 10) * 2]
    if not pool:
        return []
    try:
        recs = oa.works_by_ids([r for r, _ in pool], kind="older", origin="openalex:cocitation")
    except Exception as ex:
        log(f"  co-citation lookup failed: {ex}")
        return []
    n = dict(pool)
    for r in recs:
        r["cocited_by"] = n.get(r["openalex_id"], 0)
    recs.sort(key=lambda r: r["cocited_by"], reverse=True)
    return recs[: src.get("max_items", 10)]


def _finish(label, per, profile, sources, anchors, lock, ids, fresh, stats, health, corpus, backfill: bool):
    fresh.sort(key=lambda i: i["density"], reverse=True)
    pool = [i for i in fresh if i["kind"] == "citing"][:150] + [i for i in fresh if i["kind"] == "related"][:60]
    stats["screened"] = tag_items(profile, pool, log)
    chosen = select(pool, profile)
    listed = {i["id"] for v in chosen.values() for i in v}
    for it in fresh:
        rec = corpus.add(it, "new" if it["id"] in listed else "backlog", per)
        if it.get("ai"):
            rec["ai_relevance"] = it["ai"].get("relevance")
    cands = []
    for src in sources.get("candidates") or []:
        cands = _cocitation(corpus, ids, src, log)
    run = DATA / "runs" / per
    save_json(run / "candidates.json", [{k: v for k, v in i.items() if k != "summary"} for i in pool])
    save_json(run / "anchor_candidates.json", cands)
    cites = {a["key"]: a["key"] for a in anchors}
    body = issue_body(label, per, chosen, cands, stats, profile, cites, backlog=len(fresh) - len(listed))
    (run / "review.md").write_text(body, encoding="utf-8")
    prev = {h["source"]: h for h in load_json(DATA / "health.json", {}).get("sources", [])}
    for h in health:
        h["fail_streak"] = 0 if h["ok"] else prev.get(h["source"], {}).get("fail_streak", 0) + 1
    save_json(DATA / "health.json", {"period": per, "run_at": dt.datetime.utcnow().isoformat(timespec="seconds"),
                                     "sources": health, "stats": dict(stats)})
    corpus.save()
    log(f"stats: {dict(stats)}")
    print(run / "review.md")


def cmd_harvest(args):
    last = load_json(DATA / "health.json", {}).get("run_at", "")
    since_email = (dt.date.fromisoformat(last[:10]) - dt.timedelta(days=2)) if last else dt.date.today() - dt.timedelta(days=35)
    first_of_month = dt.date.today().replace(day=1)
    per = args.period or period(first_of_month - dt.timedelta(days=1))   # run on the 1st = previous month
    profile, sources, anchors, lock, ids, raw, health = _gather(False, since_email)
    _enrich(raw, ids, log)
    corpus = Corpus()
    fresh, stats, _ = _process(profile, anchors, lock, raw, corpus, per, log)
    _finish(f"Monthly review for {per}", per, profile, sources, anchors, lock, ids, fresh, stats, health, corpus, False)


def cmd_backfill(args):
    per = "founding"
    profile, sources, anchors, lock, ids, raw, health = _gather(True, dt.date.today())
    _enrich(raw, ids, log)
    corpus = Corpus()
    fresh, stats, _ = _process(profile, anchors, lock, raw, corpus, per, log)
    _finish("Founding review: the existing literature citing the anchors", per, profile, sources, anchors, lock,
            ids, fresh, stats, health, corpus, True)


# ------------------------------------------------------------------ publish
def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _promote(cands: list[dict], lock: dict, anchors: list[dict]) -> list[str]:
    keys = {a["key"] for a in anchors}
    lines, added = [], []
    for c in cands:
        sur = _slug((c.get("authors") or ["anon"])[0].split()[-1])
        key = f"{sur}-{c.get('year') or 'nd'}"
        while key in keys:
            key += "b"
        keys.add(key)
        by = (c.get("authors") or ["Anon."])[0].split()[-1] + (" et al." if len(c.get("authors", [])) > 1 else "")
        entry = {"key": key, "cite": f"{by}, '{c['title'][:80]}' ({c.get('year', 'n.d.')})", "title": c["title"],
                 "authors": c.get("authors", [])[:4], "year": c.get("year") or 0,
                 "added": f"{dt.date.today().isoformat()} (promoted from co-citation)"}
        dumped = yaml.safe_dump([entry], allow_unicode=True, sort_keys=False, width=200)
        lines += ["  " + ln for ln in dumped.splitlines()]
        lock["anchors"][key] = {"id": c["openalex_id"], "matched": c["title"], "year": c.get("year"),
                                "cited_by_count": c.get("cited_by_count", 0), "manual": True, "check": False}
        added.append(key)
    if lines:
        p = CONFIG / "anchors.yaml"
        p.write_text(p.read_text(encoding="utf-8").rstrip("\n") + "\n\n  # --- promoted from reviews ---\n"
                     + "\n".join(lines) + "\n", encoding="utf-8")
    return added


def cmd_publish(args):
    body = Path(args.body).read_text(encoding="utf-8")
    per = period_from_body(body) or args.period
    if not per:
        sys.exit("no period marker in the review body")
    run = DATA / "runs" / per
    cands = {c["id"]: c for c in load_json(run / "candidates.json", [])}
    anchor_cands = {c["openalex_id"]: c for c in load_json(run / "anchor_candidates.json", [])}
    approved, featured, promote = parse_review(body)
    corpus = Corpus()
    items = []
    for iid in approved:
        it = cands.get(iid)
        if not it:
            continue
        items.append({k: it.get(k) for k in ("id", "kind", "title", "url", "doi", "authors", "venue", "source", "date",
                                             "year", "anchors", "terms", "density", "trust", "work_type") if it.get(k) is not None}
                     | {"note": (it.get("ai") or {}).get("note", ""), "featured": iid in featured})
        if iid in corpus.works:
            corpus.works[iid]["status"] = "published"
            corpus.works[iid]["published"] = per
    listed = re.findall(r"<!--id:([\w-]+)-->", body)
    for iid in listed:
        if iid in corpus.works and iid not in approved:
            corpus.works[iid]["status"] = "passed"
    lock, anchors = load_lock(), load_anchors()
    chosen = [anchor_cands[c] for c in promote if c in anchor_cands]
    added = _promote(chosen, lock, anchors)
    save_lock(lock)
    for c, key in zip(chosen, added):
        rec = corpus.add({**c, "kind": "older"}, "published", per)
        rec["published"] = per
        items.append({k: c.get(k) for k in ("id", "title", "url", "doi", "authors", "venue", "source", "date", "year")}
                     | {"kind": "older", "note": f"Promoted to anchor ({key}); cited by {c.get('cocited_by')} works in the corpus.",
                        "anchors": [], "terms": [], "featured": False})
    seen = set(load_json(DATA / "candidates_seen.json", [])) | set(anchor_cands)
    save_json(DATA / "candidates_seen.json", sorted(seen))
    for p in sorted((DATA / "suggestions").glob("*.json")):
        s = load_json(p, None)
        if s and not s.get("published_in"):
            items.append(s)
            s["published_in"] = per
            save_json(p, s)
    items.sort(key=lambda i: (not i.get("featured"), {"citing": 0, "related": 1, "older": 2}.get(i.get("kind"), 3),
                              -(i.get("density") or 0)))
    save_json(DATA / "accessions" / f"{per}.json",
              {"period": per, "published_at": dt.datetime.utcnow().isoformat(timespec="seconds"), "items": items})
    corpus.save()
    log(f"published {per}: {len(items)} works; promoted anchors: {added or 'none'}")
    site.build(log)


# ------------------------------------------------------------------ terms, lineage, forms
def cmd_terms(args):
    lock = cmd_resolve()
    ids = list(oa.anchor_ids(lock))
    focus = []
    for w in oa.works_by_ids(ids):
        focus.append(w["title"] + " " + w.get("summary", ""))
    for i in ids:
        try:
            for w in oa.paged(f"cites:{i},has_abstract:true", cap=args.per_anchor,
                              select="title,abstract_inverted_index"):
                focus.append((w.get("title") or "") + " " + oa.abstract(w.get("abstract_inverted_index")))
        except Exception as ex:
            log(f"  {i}: {ex}")
    base = oa.baseline_sample(args.baseline)
    known = {v for vs in Vocabulary(load_profile()).groups.values() for v in vs}
    sug = suggest_terms(focus, base, known)
    write_yaml(DATA / "terms_suggested.yaml",
               {"generated": dt.date.today().isoformat(), "focus_docs": len(focus), "baseline_docs": len(base),
                "how_to_read": "z = how much more typical of the anchor literature than of social science at large. "
                               "Copy good terms into config/observatory.yaml -> terms.",
                "suggestions": sug})
    log(f"wrote data/terms_suggested.yaml ({len(sug)} suggestions from {len(focus)} focus / {len(base)} baseline docs)")


def cmd_lineage(args):
    from . import lineage
    lock = load_lock()
    target = args.target
    if re.fullmatch(r"W\d+", target):
        wid, key, label = target, target, target
    else:
        info = (lock.get("anchors") or {}).get(target) or {}
        if not info.get("id"):
            lock = cmd_resolve()
            info = lock["anchors"].get(target) or {}
        if not info.get("id"):
            sys.exit(f"anchor {target} has no OpenAlex id yet")
        wid, key = info["id"], target
        label = next((a["cite"] for a in load_anchors() if a["key"] == target), target)
    lineage.run(wid, key, label, cap=args.cap, log=log)
    site.build(log)


def cmd_suggest(args):
    f = parse_form(Path(args.body).read_text(encoding="utf-8"))
    url = f.get("Link", "").strip()
    if not url.startswith("http"):
        sys.exit("suggestion has no valid link")
    it = make_item(kind="related", title=f.get("Title", "") or url, url=url, source=f.get("Journal, publisher or outlet", ""),
                   date=dt.date.today().isoformat(), origin="suggestion", extra={"suggested_by": args.user})
    it.pop("summary", None)
    it["note"] = f.get("Why it matters (one line)", "")[:240]
    save_json(DATA / "suggestions" / f"{it['id']}.json", it)


def cmd_build(args):
    site.build(log)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="observatory")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("resolve")
    sub.add_parser("backfill")
    h = sub.add_parser("harvest"); h.add_argument("--period")
    p = sub.add_parser("publish"); p.add_argument("body"); p.add_argument("--period")
    sub.add_parser("build")
    t = sub.add_parser("terms"); t.add_argument("--per-anchor", type=int, default=150); t.add_argument("--baseline", type=int, default=2000)
    l = sub.add_parser("lineage"); l.add_argument("target"); l.add_argument("--cap", type=int, default=4000)
    s = sub.add_parser("suggest"); s.add_argument("body"); s.add_argument("user")
    a = ap.parse_args(argv)
    {"resolve": cmd_resolve, "backfill": cmd_backfill, "harvest": cmd_harvest, "publish": cmd_publish,
     "build": cmd_build, "terms": cmd_terms, "lineage": cmd_lineage, "suggest": cmd_suggest}[a.cmd](a)
