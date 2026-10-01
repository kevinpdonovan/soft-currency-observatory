"""Offline end-to-end test: every network call is faked.

Covers anchor resolution, backfill, monthly harvest (OpenAlex citing + related, Semantic
Scholar), de-duplication, trust, density, the review body, publishing (incl. promoting a
candidate anchor), lineage, term suggestion, and the site build.
Run:  python tests/test_pipeline.py
"""
import datetime as dt, hashlib, os, re, shutil, sys, tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp()) / "sco"
shutil.copytree(SRC, TMP, ignore=shutil.ignore_patterns("site", "data", "__pycache__", ".git"))
os.environ["SCO_ROOT"] = str(TMP)
sys.path.insert(0, str(TMP))

import yaml  # noqa: E402
from observatory import openalex as oa, scholarly_extra as sx, cli, tagger  # noqa: E402

TODAY = dt.date.today()
ANCHORS = yaml.safe_load((TMP / "config/anchors.yaml").read_text())["anchors"]


def wid(s):
    return "W" + str(int(hashlib.md5(s.encode()).hexdigest()[:8], 16))


A_ID = {a["key"]: wid(a["key"]) for a in ANCHORS}
CITES = {"guyer-2004": 700, "strange-1971": 400, "parry-bloch-1989": 3000}


def work(i, title, year, refs, venue="Economy and Society", publisher="Taylor & Francis", vtype="journal",
         abstract="", author="A. Scholar", cited=3, field="Social Sciences"):
    return {"id": f"https://openalex.org/W{i}", "doi": f"https://doi.org/10.1/t{i}", "title": title,
            "publication_date": f"{year}-05-01", "publication_year": year, "cited_by_count": cited,
            "authorships": [{"author": {"id": f"https://openalex.org/A{i}", "display_name": author}}],
            "primary_location": {"landing_page_url": f"https://ex.org/{i}",
                                 "source": {"id": "https://openalex.org/S1", "display_name": venue, "type": vtype,
                                            "host_organization_name": publisher}},
            "abstract_inverted_index": {w: [n] for n, w in enumerate(abstract.split())},
            "type": "article", "referenced_works": [f"https://openalex.org/{r}" for r in refs],
            "primary_topic": {"field": {"display_name": field}, "subfield": {"display_name": "Anthropology"}}}


OLD_CLASSIC = "W999"  # cited by many corpus works, not an anchor -> candidate anchor
CITING = [
    work(1, "Dollars under the mattress: monetary repertoires and the parallel exchange rate in Harare", TODAY.year,
         [A_ID["guyer-2004"], A_ID["jones-2010"], OLD_CLASSIC],
         abstract="ethnography of dollarization parallel exchange rate and currency substitution in Zimbabwe", cited=4),
    work(2, "Sterling balances and the end of empire in East Africa", TODAY.year - 1,
         [A_ID["strange-1971"], A_ID["mwangi-2001"], OLD_CLASSIC], venue="Journal of African History",
         publisher="Cambridge University Press", abstract="sterling area currency board blocked balances decolonisation"),
    work(3, "Consumer trust in banking apps", TODAY.year, [A_ID["parry-bloch-1989"], OLD_CLASSIC],
         venue="Some Journal", publisher="Unknown House", abstract="survey of consumer trust in apps"),
    work(4, "Soft money and campaign finance in US elections", TODAY.year, [A_ID["parry-bloch-1989"]]),
    work(5, "Predatory study of currency hierarchy", TODAY.year, [A_ID["guyer-2004"], OLD_CLASSIC],
         venue="Journal of Economics and Sustainable Development", publisher="IISTE", abstract="currency hierarchy"),
    work(6, "Cowries, conversion and credit in the Bight of Benin", TODAY.year - 2,
         [A_ID["guyer-2004"], A_ID["hopkins-1970"], OLD_CLASSIC], venue="Africa",
         abstract="cowries colonial currency and multiple currencies", cited=40),
]
RELATED = [
    work(20, "Currency hierarchy and monetary sovereignty in the CFA franc zone", TODAY.year, [],
         venue="Review of International Political Economy", abstract="currency hierarchy monetary sovereignty cfa franc"),
    work(21, "Machine learning for exchange rate prediction", TODAY.year, [], venue="Review of International Political Economy",
         abstract="exchange rate deep learning neural network"),
]


def fake_get(path, params, tries=4):
    f = params.get("filter", "") or ""
    if path == "/authors":
        return {"results": [{"id": "https://openalex.org/A1", "display_name": params["search"], "works_count": 50,
                             "topics": [{"count": 10, "field": {"display_name": "Social Sciences"}}], "last_known_institutions": []}]}
    if path == "/sources":
        return {"results": [{"id": "https://openalex.org/S1", "display_name": params["search"]}]}
    if "group_by" in params:
        return {"group_by": [{"key_display_name": "Social Sciences", "count": 30}, {"key_display_name": "Arts and Humanities", "count": 12}]}
    if "sample" in params:
        return {"results": [{"title": f"Labour markets and education outcomes {n}", "abstract_inverted_index":
                             {"school": [0], "wages": [1], "survey": [2]}} for n in range(200)]}
    if params.get("search") and not f.startswith(("from_publication", "doi")):
        s = params["search"].lower()
        for a in ANCHORS:
            if re.sub(r"[^\w\s]", " ", a["title"]).lower()[:25] in s or s[:25] in re.sub(r"[^\w\s]", " ", a["title"]).lower():
                if a["key"] == "shipton-1989":
                    return {"results": []}   # an unmatched anchor
                return {"results": [{"id": f"https://openalex.org/{A_ID[a['key']]}", "title": a["title"],
                                     "publication_year": a["year"], "cited_by_count": CITES.get(a["key"], 120),
                                     "authorships": [{"author": {"display_name": n}} for n in a["authors"]]}]}
        return {"results": []}
    if f.startswith("openalex:"):
        ids = f.split(":", 1)[1].split("|")
        if params.get("select") == "id,cited_by_count":
            return {"results": [{"id": f"https://openalex.org/{i}", "cited_by_count": CITES.get(
                next((k for k, v in A_ID.items() if v == i), ""), 120)} for i in ids]}
        out = []
        for i in ids:
            if i == OLD_CLASSIC:
                out.append(work(999, "The Great Transformation", 1944, [], venue="Beacon", publisher="Beacon Press",
                                author="Karl Polanyi", cited=30000))
            else:
                k = next((k for k, v in A_ID.items() if v == i), None)
                if k:
                    a = next(x for x in ANCHORS if x["key"] == k)
                    w = work(int(i[1:]), a["title"], a["year"], [], abstract="money currency conversion value")
                    w["id"] = f"https://openalex.org/{i}"
                    out.append(w)
        return {"results": out}
    if f.startswith("cites:"):
        if params.get("cursor") not in (None, "*"):
            return {"results": [], "meta": {}}
        ids = set(f.split(",")[0][6:].split("|"))
        res = [w for w in CITING if ids & {r.split("/")[-1] for r in w["referenced_works"]}]
        if "has_abstract" in f:
            return {"results": [{"title": w["title"], "abstract_inverted_index": w["abstract_inverted_index"]} for w in res] * 2, "meta": {}}
        return {"results": res, "meta": {"next_cursor": "x"}}
    if f.startswith("doi:"):
        return {"results": []}
    if f.startswith("from_publication_date"):
        if params.get("cursor") not in (None, "*"):
            return {"results": [], "meta": {}}
        return {"results": RELATED if params.get("search") == "currency hierarchy" else [], "meta": {"next_cursor": "x"}}
    raise AssertionError(f"unexpected OpenAlex call {path} {params}")


def fake_s2(path, params, tries=4):
    if path == "/paper/search/match":
        return {"data": [{"paperId": "P" + params["query"][:5], "year": 2004}]} if "Marginal" in params["query"] else {}
    if path.endswith("/citations") and path.startswith("/paper/PMargi"):
        return {"data": [
            {"citingPaper": {"title": "Dollars under the mattress: monetary repertoires and the parallel exchange rate in Harare",
                             "year": TODAY.year, "externalIds": {"DOI": "10.1/t1"}, "authors": [{"name": "A. Scholar"}]}},
            {"citingPaper": {"title": "Remittances, hard currency and bridewealth in western Kenya", "year": TODAY.year,
                             "externalIds": {}, "venue": "Africa", "authors": [{"name": "B. Writer"}],
                             "abstract": "remittances hard currency and earmarking of money"}}]}
    return {}


oa.get = fake_get
sx._s2_get = fake_s2
sx.time.sleep = lambda s: None
oa.time.sleep = lambda s: None


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  ok ·", msg)


print("backfill")
cli.cmd_backfill(None)
from observatory.items import Corpus, load_json  # noqa: E402
from observatory.profile import load_lock  # noqa: E402
lock = load_lock()
check(lock["anchors"]["guyer-2004"]["id"] == A_ID["guyer-2004"], "anchor resolved to OpenAlex id")
check(lock["anchors"]["shipton-1989"]["id"] is None and lock["anchors"]["shipton-1989"]["check"], "unmatched anchor flagged")
c = Corpus()
titles = {w["title"] for w in c.works.values()}
check(not any("campaign finance" in t for t in titles), "negative keyword dropped ('soft money')")
check(not any("Predatory" in t for t in titles), "deny-listed publisher dropped")
check(any("bridewealth" in t for t in titles), "Semantic Scholar-only work entered corpus")
harare = [w for w in c.works.values() if "Harare" in w["title"]]
check(len(harare) == 1, "OpenAlex and Semantic Scholar copies merged")
check(set(harare[0]["anchors"]) == {"guyer-2004", "jones-2010"}, "anchors cited recorded")
check("parallel exchange rate" in harare[0]["terms"] and "dollarization" in harare[0]["terms"], "terms matched (variants grouped)")
bank = next(w for w in c.works.values() if "banking apps" in w["title"])
check(bank["density"] < harare[0]["density"], "widely cited anchor weighs less than narrow ones")
check(bank["trust"] == "unknown", "unknown venue marked")
body = (TMP / "data/runs/founding/review.md").read_text()
check("observatory:period=founding" in body and "Candidate anchors" in body, "founding review body written")
check("<!--cand:W999-->" in body, "co-cited classic proposed as candidate anchor")
bank_line = next(ln for ln in body.splitlines() if "banking apps" in ln)
check(bank_line.startswith("- [ ]"), "single-anchor unknown-venue work not pre-ticked")

print("publish founding (tick all listed works + promote candidate)")
edited = re.sub(r"- \[ \] (.*?<!--id:)", r"- [x] \1", body)
edited = edited.replace("- [ ] [The Great Transformation", "- [x] [The Great Transformation")
edited = edited.replace("- [x] [Dollars under", "- [x] ★ [Dollars under")
(TMP / "rb.md").write_text(edited)
cli.cmd_publish(type("A", (), {"body": str(TMP / "rb.md"), "period": None}))
acc = load_json(TMP / "data/accessions/founding.json", {})
check(any(i.get("featured") for i in acc["items"]), "featured work kept")
check(any(i["kind"] == "older" and "Great Transformation" in i["title"] for i in acc["items"]), "promoted candidate published as older work")
check("polanyi-1944" in (TMP / "config/anchors.yaml").read_text(), "promoted candidate appended to anchors.yaml")
yaml.safe_load((TMP / "config/anchors.yaml").read_text())
check(load_lock()["anchors"]["polanyi-1944"]["manual"], "promoted anchor locked")

print("monthly harvest")
CITING.append(work(7, "A new study of monetary sovereignty and dollarization in Ecuador", TODAY.year,
                   [A_ID["helleiner-2003"], OLD_CLASSIC], venue="Focaal", publisher="Berghahn",
                   abstract="dollarization monetary sovereignty"))
cli.cmd_harvest(type("A", (), {"period": "2026-10"}))
c = Corpus()
check(sum(1 for w in c.works.values() if "Harare" in w["title"]) == 1, "already-seen work not duplicated")
body2 = (TMP / "data/runs/2026-10/review.md").read_text()
check("Ecuador" in body2 and "Harare" not in body2, "only new works reviewed")
check("CFA franc zone" in body2 and "Machine learning" not in body2, "related work gated by terms and negatives")
check("<!--cand:W999-->" not in body2, "promoted candidate not proposed again")
(TMP / "rb2.md").write_text(body2.replace("- [ ]", "- [x]"))
cli.cmd_publish(type("A", (), {"body": str(TMP / "rb2.md"), "period": None}))

print("tools")
cli.cmd_lineage(type("A", (), {"target": "guyer-2004", "cap": 100}))
check((TMP / "data/lineage/guyer-2004.md").exists(), "lineage report written")
cli.cmd_terms(type("A", (), {"per_anchor": 20, "baseline": 200}))
sug = yaml.safe_load((TMP / "data/terms_suggested.yaml").read_text())
check(sug["suggestions"] and sug["suggestions"][0]["z"] > 0, "term suggestions ranked")

print("site")
site = TMP / "site"
for p in ["index.html", "research.html", "seminar.html", "archive.html", "readings.html", "lexicon.html",
          "density.html", "join.html", "about.html", "accessions/founding.html", "accessions/2026-10.html",
          "lineage/guyer-2004.html", "feed.xml", "items.json", "anchors.json"]:
    check((site / p).exists(), f"built {p}")
idx = (site / "index.html").read_text()
check("<em>soft</em>" in idx and "Harare" in idx or "Ecuador" in idx, "home shows premise and latest accessions")
check("Check match" in (site / "density.html").read_text() or "Unmatched" in (site / "density.html").read_text(), "density flags anchor matches to check")
print(f"\nALL PASSED  (site at {site})")
