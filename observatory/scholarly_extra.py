"""Second and third citation sources: Semantic Scholar, and Google Scholar alert emails.

Google Scholar has no API and must not be scraped. Its "new citations" alerts, sent to a
dedicated inbox, are the permitted route and the best coverage for monographs.
"""
from __future__ import annotations

import datetime as dt
import re
import time
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import requests
from dateutil import parser as dparser

from .items import make_item
from .profile import env
from .terms import norm

S2 = "https://api.semanticscholar.org/graph/v1"
S2_FIELDS = "title,year,publicationDate,externalIds,authors,venue,url,abstract"


def _s2_get(path: str, params: dict, tries: int = 4) -> dict:
    headers = {"x-api-key": env("SEMANTIC_SCHOLAR_API_KEY")} if env("SEMANTIC_SCHOLAR_API_KEY") else {}
    for n in range(tries):
        r = requests.get(f"{S2}{path}", params=params, headers=headers, timeout=40)
        if r.status_code == 429 and n < tries - 1:
            time.sleep(3 * (n + 1))
            continue
        if r.status_code == 404:
            return {}
        r.raise_for_status()
        return r.json()
    return {}


def _s2_paper_id(anchor: dict, lock_info: dict) -> str | None:
    if lock_info.get("s2_id"):
        return lock_info["s2_id"]
    if anchor.get("doi"):
        return "DOI:" + anchor["doi"].replace("https://doi.org/", "")
    js = _s2_get("/paper/search/match", {"query": anchor["title"], "fields": "paperId,title,year"})
    data = (js.get("data") or [])
    if data and abs((data[0].get("year") or 0) - int(anchor.get("year") or 0)) <= 3:
        lock_info["s2_id"] = data[0]["paperId"]
        return data[0]["paperId"]
    return None


def s2_citing(anchors: list[dict], lock: dict, since: dt.date | None, cap: int = 1000, log=print) -> list[dict]:
    out = []
    pause = 1.1 if not env("SEMANTIC_SCHOLAR_API_KEY") else 0.2
    for a in anchors:
        info = lock["anchors"].get(a["key"]) or {}
        try:
            pid = _s2_paper_id(a, info)
            time.sleep(pause)
            if not pid:
                continue
            offset = 0
            while offset < cap:
                js = _s2_get(f"/paper/{pid}/citations", {"fields": S2_FIELDS, "limit": 100, "offset": offset})
                rows = js.get("data") or []
                for row in rows:
                    p = row.get("citingPaper") or {}
                    year = p.get("year") or 0
                    if since and year and year < since.year:
                        continue
                    if not p.get("title"):
                        continue
                    ext = p.get("externalIds") or {}
                    it = make_item(kind="citing", title=p["title"], url=p.get("url") or "",
                                   doi=ext.get("DOI") or "", source=p.get("venue") or "Semantic Scholar",
                                   venue=p.get("venue") or "", date=p.get("publicationDate") or (f"{year}-01-01" if year else ""),
                                   authors=[x.get("name") for x in p.get("authors") or [] if x.get("name")],
                                   summary=p.get("abstract") or "", origin="s2:citing",
                                   extra={"year": year or None})
                    it["anchors"] = [a["key"]]
                    out.append(it)
                if not js.get("next") or not rows:
                    break
                offset = js["next"]
                time.sleep(pause)
        except Exception as ex:
            log(f"  s2 {a['key']}: {ex}")
        if a["key"] in lock["anchors"] and info:
            lock["anchors"][a["key"]] = info
    return out


# ------------------------------------------------------------------ email (Google Scholar alerts)
class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self._href, self._text = [], None, []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href, self._text = dict(attrs).get("href"), []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href:
            self.links.append((self._href, re.sub(r"\s+", " ", "".join(self._text)).strip()))
            self._href = None


def _unwrap(url: str) -> str:
    q = parse_qs(urlsplit(url).query)
    for key in ("url", "u", "q", "target"):
        v = q.get(key, [""])[0]
        if v.startswith("http"):
            return v
    return url


def _body(msg) -> str:
    parts = []
    for part in msg.walk():
        if part.get_content_type() in ("text/html", "text/plain") and not part.get_filename():
            try:
                parts.append(part.get_content())
            except Exception:
                parts.append((part.get_payload(decode=True) or b"").decode("utf-8", "replace"))
    return "\n".join(parts)


def anchor_from_subject(subject: str, anchors: list[dict]) -> str | None:
    """Scholar citation alerts name the cited work in the subject line."""
    s = set(re.findall(r"[a-z0-9]+", norm(subject)))
    best, score = None, 0.0
    for a in anchors:
        t = set(re.findall(r"[a-z0-9]+", norm(a["title"].split(":")[0])))
        sc = len(s & t) / max(1, len(t))
        if sc > score:
            best, score = a["key"], sc
    return best if score >= 0.7 else None


def email_alerts(src: dict, anchors: list[dict], since: dt.date, log=print) -> tuple[list[dict], str]:
    import email
    import imaplib
    from email import policy

    host, user, pwd = env("IMAP_HOST", "imap.gmail.com"), env("IMAP_USER"), env("IMAP_PASSWORD")
    if not (user and pwd):
        return [], "not configured (no IMAP_USER / IMAP_PASSWORD secrets)"
    out = []
    M = imaplib.IMAP4_SSL(host)
    M.login(user, pwd)
    M.select(src.get("folder", "INBOX"), readonly=True)
    typ, data = M.search(None, f'(SINCE "{since.strftime("%d-%b-%Y")}" FROM "{src["from"]}")')
    for mid in (data[0] or b"").split()[-src.get("max_messages", 200):]:
        typ, msgdata = M.fetch(mid, "(RFC822)")
        msg = email.message_from_bytes(msgdata[0][1], policy=policy.default)
        subj = str(msg.get("Subject") or "")
        key = anchor_from_subject(subj, anchors)
        try:
            d = dparser.parse(str(msg.get("Date")), fuzzy=True).date().isoformat()
        except Exception:
            d = ""
        p = _Links()
        p.feed(_body(msg))
        for href, text in p.links:
            if len(text) < src.get("min_title_len", 20) or re.search(
                    r"unsubscribe|cancel alert|view all|settings|privacy|google scholar", text, re.I):
                continue
            it = make_item(kind="citing", title=text, url=_unwrap(href), source=src.get("label", "Google Scholar"),
                           date=d, origin="email:scholar", extra={"alert_subject": subj[:200]})
            it["anchors"] = [key] if key else []
            out.append(it)
    M.logout()
    return out, ""
