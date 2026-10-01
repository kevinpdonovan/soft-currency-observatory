"""Citation lineage of one work: who cites it, and which of those are themselves widely cited.

  python -m observatory lineage guyer-2004        # an anchor key
  python -m observatory lineage W1234567890       # any OpenAlex work id

Writes data/lineage/<key>.json and <key>.md; the site shows a page per lineage.

Measures
  descendants        works citing the focal work (OpenAlex; books are under-counted)
  influence          a descendant's own citations, and citations per year since publication
                     (so recent work is not buried under old work)
  carriers           authors whose citing works are most cited in turn: the people through
                     whom the focal work reaches wider audiences
  travel             fields of the descendants by period, and fields of the works citing the
                     top descendants (second generation): how far the ideas move from home
"""
from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict

from . import openalex as oa
from .items import DATA, save_json

SEL = "id,doi,title,publication_year,authorships,cited_by_count,primary_location,primary_topic,type"


def _field(w: dict) -> str:
    return (((w.get("primary_topic") or {}).get("field") or {}).get("display_name")) or "Unclassified"


def _subfield(w: dict) -> str:
    return (((w.get("primary_topic") or {}).get("subfield") or {}).get("display_name")) or "Unclassified"


def _group_fields(wid: str) -> list[dict]:
    try:
        js = oa.get("/works", {"filter": f"cites:{wid}", "group_by": "primary_topic.field.id"})
        return [{"field": g.get("key_display_name"), "count": g.get("count", 0)} for g in js.get("group_by", [])]
    except Exception:
        return []


def run(focal_id: str, key: str, label: str, cap: int = 4000, second_gen: int = 12, log=print) -> dict:
    this_year = dt.date.today().year
    focal = oa.get("/works", {"filter": f"openalex:{focal_id}", "select": SEL}).get("results", [{}])[0]
    ws = oa.paged(f"cites:{focal_id}", cap=cap, select=SEL)
    log(f"lineage {key}: {len(ws)} citing works")
    desc = []
    for w in ws:
        y = w.get("publication_year") or this_year
        c = w.get("cited_by_count", 0) or 0
        desc.append({
            "id": oa.short(w["id"]), "title": w.get("title") or "", "year": y, "cited_by": c,
            "per_year": round(c / max(1, this_year - y + 1), 2),
            "authors": [a["author"]["display_name"] for a in w.get("authorships") or [] if (a.get("author") or {}).get("display_name")],
            "author_ids": [oa.short(a["author"]["id"]) for a in w.get("authorships") or [] if (a.get("author") or {}).get("id")],
            "venue": ((w.get("primary_location") or {}).get("source") or {}).get("display_name") or "",
            "field": _field(w), "subfield": _subfield(w), "type": w.get("type", ""),
            "url": w.get("doi") or w["id"],
        })
    by_year = Counter(d["year"] for d in desc)
    periods = defaultdict(Counter)
    for d in desc:
        p = f"{(d['year'] // 5) * 5}–{(d['year'] // 5) * 5 + 4}"
        periods[p][d["field"]] += 1
    subfields = Counter(d["subfield"] for d in desc)

    authors = defaultdict(lambda: {"works": 0, "cited_by": 0, "name": "", "top": ""})
    for d in desc:
        for name, aid in zip(d["authors"], d["author_ids"]):
            a = authors[aid]
            a["name"] = name
            a["works"] += 1
            a["cited_by"] += d["cited_by"]
            if not a["top"] or d["cited_by"] > a.get("_topc", -1):
                a["top"], a["_topc"] = d["title"], d["cited_by"]
    carriers = sorted(({"id": k, **{x: v for x, v in a.items() if not x.startswith("_")}} for k, a in authors.items()),
                      key=lambda a: a["cited_by"], reverse=True)[:30]

    top_total = sorted(desc, key=lambda d: d["cited_by"], reverse=True)[:30]
    top_rate = sorted([d for d in desc if this_year - d["year"] >= 1], key=lambda d: d["per_year"], reverse=True)[:30]

    second = []
    for d in top_total[:second_gen]:
        second.append({"id": d["id"], "title": d["title"], "fields": _group_fields(d["id"])})
    home = _group_fields(focal_id)

    out = {
        "key": key, "label": label, "focal": {"id": focal_id, "title": focal.get("title"),
                                             "year": focal.get("publication_year"), "cited_by": focal.get("cited_by_count")},
        "generated": dt.date.today().isoformat(), "n_descendants": len(desc),
        "second_generation_reach": sum(d["cited_by"] for d in desc),
        "by_year": dict(sorted(by_year.items())), "fields_by_period": {p: dict(c.most_common()) for p, c in sorted(periods.items())},
        "top_subfields": subfields.most_common(15), "top_by_citations": top_total, "top_by_rate": top_rate,
        "carriers": carriers, "home_fields": home, "second_generation_fields": second,
        "caveat": ("OpenAlex under-counts citations to and from books and non-English work; treat counts as "
                   "indicative. 'Second generation reach' double-counts works that cite several descendants."),
    }
    save_json(DATA / "lineage" / f"{key}.json", out)
    (DATA / "lineage" / f"{key}.md").write_text(to_markdown(out), encoding="utf-8")
    return out


def to_markdown(o: dict) -> str:
    L = [f"# Citation lineage: {o['label']}", "",
         f"{o['n_descendants']} citing works in OpenAlex; together they are cited {o['second_generation_reach']} times. "
         f"Generated {o['generated']}. {o['caveat']}", "", "## Most cited descendants", ""]
    L += [f"{n}. {d['title']} ({', '.join(d['authors'][:2])}, {d['year']}; {d['venue']}): cited {d['cited_by']} times, "
          f"{d['per_year']}/yr · {d['field']}" for n, d in enumerate(o["top_by_citations"][:20], 1)]
    L += ["", "## Fastest-rising descendants (citations per year)", ""]
    L += [f"{n}. {d['title']} ({', '.join(d['authors'][:2])}, {d['year']}): {d['per_year']}/yr"
          for n, d in enumerate(o["top_by_rate"][:15], 1)]
    L += ["", "## Carriers (authors whose citing work is most cited in turn)", ""]
    L += [f"{n}. {a['name']}: {a['works']} citing work(s), cited {a['cited_by']} times; best known: {a['top']}"
          for n, a in enumerate(o["carriers"][:20], 1)]
    L += ["", "## Where it travels", "", "Fields of works citing the focal work:", ""]
    L += [f"- {f['field']}: {f['count']}" for f in o["home_fields"][:10]]
    L += ["", "Fields of works citing the most cited descendants (second generation):", ""]
    for s in o["second_generation_fields"]:
        L.append(f"- *{s['title']}*: " + ", ".join(f"{f['field']} {f['count']}" for f in s["fields"][:4]))
    return "\n".join(L) + "\n"
