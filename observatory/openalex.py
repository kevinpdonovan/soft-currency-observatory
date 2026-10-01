"""OpenAlex client: anchors, forward citations, keyword/journal/author sweeps, lookups.

Only metadata is collected. Abstracts are used for term matching and never published.
"""
from __future__ import annotations

import datetime as dt
import re
import time

import requests

from .items import make_item
from .profile import env
from .terms import norm

OA = "https://api.openalex.org"
TIMEOUT = 40
UA = "SoftCurrencyObservatory/0.1 (academic research archive; mailto:{mail})"
SELECT = ("id,doi,title,publication_date,publication_year,authorships,primary_location,"
          "abstract_inverted_index,type,referenced_works,language,cited_by_count,primary_topic")
WORK_TYPES = "type:article|preprint|book|book-chapter|report|review|dissertation"
# Social Sciences, Economics/Econometrics/Finance, Arts & Humanities, Business
FIELDS = "primary_topic.field.id:fields/33|fields/20|fields/12|fields/14"

S = requests.Session()
S.headers["User-Agent"] = UA.format(mail=env("CONTACT_EMAIL", "unset"))


def get(path: str, params: dict, tries: int = 4) -> dict:
    p = dict(params)
    if env("CONTACT_EMAIL"):
        p["mailto"] = env("CONTACT_EMAIL")
    if env("OPENALEX_API_KEY"):
        p["api_key"] = env("OPENALEX_API_KEY")
    for n in range(tries):
        r = S.get(f"{OA}{path}", params=p, timeout=TIMEOUT)
        if r.status_code in (429, 500, 502, 503) and n < tries - 1:
            time.sleep(2 ** n * 2)
            continue
        r.raise_for_status()
        return r.json()
    return {}


def short(oid: str) -> str:
    return (oid or "").split("/")[-1]


def abstract(inv: dict | None) -> str:
    if not inv:
        return ""
    pos = {}
    for word, idxs in inv.items():
        for i in idxs:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos))[:2000]


def oa_to_item(w: dict, kind: str, origin: str, extra=None) -> dict:
    loc = w.get("primary_location") or {}
    src = loc.get("source") or {}
    venue = src.get("display_name") or ""
    authors = [a["author"]["display_name"] for a in (w.get("authorships") or [])
               if (a.get("author") or {}).get("display_name")]
    topic = w.get("primary_topic") or {}
    return make_item(
        kind=kind, title=w.get("title") or "", url=loc.get("landing_page_url") or w.get("id", ""),
        doi=w.get("doi") or "", source=venue or "OpenAlex", venue=venue,
        date=w.get("publication_date") or "", authors=authors,
        summary=abstract(w.get("abstract_inverted_index")), origin=origin,
        extra={"openalex_id": short(w.get("id", "")), "year": w.get("publication_year"),
               "work_type": w.get("type", ""), "cited_by_count": w.get("cited_by_count", 0),
               "referenced_works": [short(r) for r in (w.get("referenced_works") or [])][:200],
               "venue_id": short(src.get("id") or ""), "venue_type": src.get("type") or "",
               "publisher": src.get("host_organization_name") or "",
               "venue_doaj": bool(src.get("is_in_doaj")),
               "field": ((topic.get("field") or {}).get("display_name") or ""),
               **(extra or {})},
    )


def paged(filter_str: str, search: str | None = None, cap: int = 200, select: str = SELECT,
          sort: str | None = None) -> list[dict]:
    out, cursor = [], "*"
    while cursor and len(out) < cap:
        params = {"filter": filter_str, "per-page": 100 if cap > 25 else 25, "select": select, "cursor": cursor}
        if search:
            params["search"] = search
        if sort:
            params["sort"] = sort
        js = get("/works", params)
        res = js.get("results", [])
        out += res
        cursor = (js.get("meta") or {}).get("next_cursor")
        if not res:
            break
    return out[:cap]


# ------------------------------------------------------------------ anchors
def _tok(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", norm(s)) if len(w) > 2}


def _surname(name: str) -> str:
    parts = re.findall(r"[a-z]+", norm(name))
    return parts[-1] if parts else ""


def match_score(anchor: dict, w: dict) -> float:
    """0..1: title overlap (main title before any colon counts most), author surname, year."""
    at, wt = _tok(anchor["title"]), _tok(w.get("title") or "")
    main = _tok(anchor["title"].split(":")[0])
    title = len(at & wt) / max(1, len(at | wt))
    main_cov = len(main & wt) / max(1, len(main))
    names = {_surname(a) for a in anchor.get("authors", [])}
    wnames = {_surname((a.get("author") or {}).get("display_name", "")) for a in (w.get("authorships") or [])}
    author = 1.0 if names & wnames else 0.0
    yd = abs((w.get("publication_year") or 0) - int(anchor.get("year") or 0))
    year = 1.0 if yd <= 1 else 0.5 if yd <= 3 else 0.0
    return round(0.35 * title + 0.25 * main_cov + 0.25 * author + 0.15 * year, 3)


def resolve_anchors(anchors: list[dict], lock: dict, log=print) -> None:
    """Match each anchor to an OpenAlex work once; cache in the lock. Refresh citation counts."""
    table = lock["anchors"]
    for a in anchors:
        cur = table.get(a["key"])
        if cur and (cur.get("manual") or cur.get("id")):
            continue
        sel = "id,title,publication_year,authorships,cited_by_count,type,doi"
        cands = []
        try:
            if a.get("doi"):
                doi = a["doi"].replace("https://doi.org/", "")
                cands = get("/works", {"filter": f"doi:{doi}", "select": sel}).get("results", [])
            if not cands:
                q = re.sub(r"[^\w\s]", " ", a["title"].split(":")[0] if len(a["title"]) > 90 else a["title"])
                y = int(a.get("year") or 0)
                flt = f"publication_year:{y - 3}-{y + 3}" if y else None
                params = {"search": q, "select": sel, "per-page": 15}
                if flt:
                    params["filter"] = flt
                cands = get("/works", params).get("results", [])
        except Exception as ex:
            log(f"  anchor {a['key']}: lookup failed ({ex}); will retry next run")
            continue
        scored = sorted(((match_score(a, w), w) for w in cands), key=lambda t: t[0], reverse=True)
        if not scored or scored[0][0] < 0.45:
            table[a["key"]] = {"id": None, "matched": None, "check": True,
                               "note": "no confident match in OpenAlex; add the id by hand"}
            log(f"  anchor {a['key']}: NO MATCH")
            continue
        s, w = scored[0]
        # OpenAlex sometimes splits one book into several records; keep close runners-up too
        extra = [short(x["id"]) for sc, x in scored[1:4] if sc >= s - 0.05 and sc >= 0.6]
        table[a["key"]] = {"id": short(w["id"]), "also": extra, "matched": w.get("title"),
                           "year": w.get("publication_year"), "cited_by_count": w.get("cited_by_count", 0),
                           "score": s, "check": bool(s < 0.8 or a.get("verify") or extra)}
        log(f"  anchor {a['key']}: {short(w['id'])} ({s})")
        time.sleep(0.15)
    # refresh citation counts (used for weights). Books are often split across several
    # OpenAlex records (the book, Choice and journal reviews carrying its citations), so the
    # count is the sum over the primary record and everything listed under "also".
    owner = {}
    for k, info in table.items():
        if info and info.get("id"):
            for x in [info["id"]] + list(info.get("also") or []):
                owner[x] = k
    totals = {}
    idl = list(owner)
    for i in range(0, len(idl), 50):
        try:
            js = get("/works", {"filter": "openalex:" + "|".join(idl[i:i + 50]), "per-page": 50,
                                "select": "id,cited_by_count"})
            for w in js.get("results", []):
                k = owner[short(w["id"])]
                totals[k] = totals.get(k, 0) + (w.get("cited_by_count", 0) or 0)
        except Exception:
            pass
    for k, n in totals.items():
        table[k]["cited_by_count"] = n


def anchor_ids(lock: dict) -> dict[str, str]:
    """OpenAlex id -> anchor key (including split duplicate records)."""
    out = {}
    for key, info in (lock.get("anchors") or {}).items():
        if not info or not info.get("id"):
            continue
        out[info["id"]] = key
        for x in info.get("also") or []:
            out[x] = key
    return out


# ------------------------------------------------------------------ forward citations
def _attach_anchors(it: dict, ids: dict[str, str]) -> dict:
    it["anchors"] = sorted({ids[r] for r in it.get("referenced_works", []) if r in ids})
    return it


def citing(ids: dict[str, str], since: dt.date | None, cap: int = 3000, per_anchor: bool = False,
           log=print) -> list[dict]:
    """Works citing any anchor. per_anchor=True (backfill) queries each anchor separately so a
    heavily cited anchor cannot crowd out the others."""
    base = f"{WORK_TYPES}" + (f",from_publication_date:{since.isoformat()}" if since else "")
    if env("OPENALEX_CREATED_FILTER") and since:   # premium filter: catches late-indexed works exactly
        base = f"{WORK_TYPES},from_created_date:{since.isoformat()}"
    out = []
    keys = list(ids)
    groups = [[k] for k in keys] if per_anchor else [keys[i:i + 40] for i in range(0, len(keys), 40)]
    for g in groups:
        try:
            for w in paged(f"cites:{'|'.join(g)},{base}", cap=cap):
                out.append(_attach_anchors(oa_to_item(w, "citing", "openalex:citing"), ids))
        except Exception as ex:
            log(f"  citing {g[:3]}…: {ex}")
        time.sleep(0.1)
    return out


# ------------------------------------------------------------------ related sweeps
def _resolve_names(kind: str, names: list[str], lock: dict) -> list[str]:
    table = lock[kind]
    ids = []
    for name in names:
        if name not in table:
            try:
                if kind == "authors":
                    res = get("/authors", {"search": name, "per-page": 10,
                                           "select": "id,display_name,works_count,topics,last_known_institutions"})["results"]
                    social = {"Social Sciences", "Economics, Econometrics and Finance", "Arts and Humanities",
                              "Business, Management and Accounting"}

                    def fit(a):
                        t = a.get("topics") or []
                        s = sum(x.get("count", 1) for x in t if (x.get("field") or {}).get("display_name") in social)
                        return s / (sum(x.get("count", 1) for x in t) or 1) + min(a.get("works_count", 0), 200) / 2000
                    res.sort(key=fit, reverse=True)
                    close = [r for r in res if fit(r) > 0.5]
                    table[name] = ({"id": short(res[0]["id"]), "matched": res[0]["display_name"],
                                    "institution": ((res[0].get("last_known_institutions") or [{}])[0] or {}).get("display_name", ""),
                                    "check": len(close) != 1} if res else None)
                else:
                    res = get("/sources", {"search": name, "per-page": 5})["results"]
                    nn = lambda s: re.sub(r"[^a-z0-9]", "", norm(s).replace("&", "and"))
                    exact = [r for r in res if nn(r["display_name"]) == nn(name)]
                    pick = (exact or res or [None])[0]
                    table[name] = ({"id": short(pick["id"]), "matched": pick["display_name"], "check": not exact}
                                   if pick else None)
            except Exception:
                continue
        if table.get(name) and table[name].get("id"):
            ids.append(table[name]["id"])
    return ids


def related(src: dict, profile: dict, lock: dict, anchor_map: dict[str, str], log=print) -> list[dict]:
    since = (dt.date.today() - dt.timedelta(days=src.get("lookback_days", 45))).isoformat()
    base = f"from_publication_date:{since},{WORK_TYPES}"
    mode, out = src.get("mode"), []
    if mode == "queries":
        for q in profile.get("research_queries", []):
            try:
                for w in paged(f"{base},{FIELDS}", q, cap=60):
                    out.append(_attach_anchors(oa_to_item(w, "related", f"openalex:query:{q}"), anchor_map))
            except Exception as ex:
                log(f"  query {q}: {ex}")
            time.sleep(0.15)
    elif mode in ("journals", "authors"):
        names = profile.get("watch_journals" if mode == "journals" else "watch_authors", [])
        ids = _resolve_names(mode, names, lock)
        key = "authorships.author.id" if mode == "authors" else "primary_location.source.id"
        for i in range(0, len(ids), 40):
            for w in paged(f"{base},{key}:{'|'.join(ids[i:i + 40])}", cap=600):
                out.append(_attach_anchors(oa_to_item(w, "related", f"openalex:{mode}",
                                                      {"watched": True}), anchor_map))
    return out


# ------------------------------------------------------------------ lookups
def works_by_ids(ids: list[str], kind: str = "older", origin: str = "openalex:lookup") -> list[dict]:
    out = []
    for i in range(0, len(ids), 50):
        js = get("/works", {"filter": "openalex:" + "|".join(ids[i:i + 50]), "per-page": 50, "select": SELECT})
        out += [oa_to_item(w, kind, origin) for w in js.get("results", [])]
    return out


def works_by_doi(dois: list[str]) -> dict[str, dict]:
    out = {}
    dois = [d for d in dict.fromkeys(dois) if d]
    for i in range(0, len(dois), 40):
        try:
            js = get("/works", {"filter": "doi:" + "|".join(dois[i:i + 40]), "per-page": 50, "select": SELECT})
        except Exception:
            continue
        for w in js.get("results", []):
            out[(w.get("doi") or "").replace("https://doi.org/", "").lower()] = w
    return out


def find_by_title(title: str) -> dict | None:
    """Best OpenAlex record for a bare title (Scholar alerts), or None if not confident."""
    try:
        res = get("/works", {"search": re.sub(r"[^\w\s]", " ", title)[:200], "per-page": 5,
                             "select": SELECT}).get("results", [])
    except Exception:
        return None
    t = _tok(title)
    for w in res:
        wt = _tok(w.get("title") or "")
        if t and len(t & wt) / max(1, len(t | wt)) >= 0.8:
            return w
    return None


def baseline_sample(n: int = 2000, seed: int = 7) -> list[str]:
    """Random titles+abstracts from social science / humanities / economics, for term suggestion."""
    docs = []
    for page in range(1, (n // 200) + 1):
        try:
            js = get("/works", {"sample": n, "seed": seed, "per-page": 200, "page": page,
                                "filter": f"{FIELDS},has_abstract:true,from_publication_date:2000-01-01",
                                "select": "title,abstract_inverted_index"})
        except Exception:
            break
        docs += [(w.get("title") or "") + " " + abstract(w.get("abstract_inverted_index")) for w in js.get("results", [])]
    return docs
