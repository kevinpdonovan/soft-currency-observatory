"""Term matching (the tracked vocabulary) and term suggestion from the anchor literature."""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from functools import lru_cache


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text.lower().replace("’", "'").replace("–", "-").replace("‐", "-"))


@lru_cache(maxsize=8192)
def _rx(term: str) -> re.Pattern:
    t = re.escape(norm(term).strip())
    return re.compile(rf"(?<![\w]){t}(?![\w])")


def hits(text: str, terms) -> list[str]:
    return [t for t in terms if t and _rx(t).search(text)]


class Vocabulary:
    """Tracked terms. A config line 'a | b | c' is one term, reported as 'a'."""

    def __init__(self, profile: dict):
        self.groups: dict[str, list[str]] = {}
        for line in profile.get("terms", []) or []:
            variants = [v.strip() for v in str(line).split("|") if v.strip()]
            if variants:
                self.groups[variants[0]] = variants
        self.negative = profile.get("negative_keywords", []) or []

    def find(self, title: str, abstract: str = "") -> list[str]:
        text = norm(title) + " \n " + norm(abstract)
        return [head for head, vs in self.groups.items() if hits(text, vs)]

    def negative_hit(self, title: str) -> bool:
        return bool(hits(norm(title), self.negative))


# ------------------------------------------------------------ term suggestion
STOP = set("""a an the of and or in on for to from by with without within into over under between among
as at is are was were be been being this that these those it its their his her our we they he she
how what why when where which who whom whose not no than then also more most less least such via
new study studies case cases analysis approach paper article book chapter review evidence toward towards
role impact effect effects use using based through during after before across can may might one two
de la le les des du et el los las en y o da do das dos e em para por con per il di""".split())


def _tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z][a-z'-]+", norm(text)) if len(w) > 2]


def ngrams(text: str, n_max: int = 3) -> set[str]:
    toks = _tokens(text)
    out = set()
    for n in range(1, n_max + 1):
        for i in range(len(toks) - n + 1):
            g = toks[i:i + n]
            if g[0] in STOP or g[-1] in STOP:
                continue
            if n == 1 and len(g[0]) < 5:
                continue
            out.add(" ".join(g))
    return out


def suggest_terms(focus_docs: list[str], baseline_docs: list[str], known: set[str],
                  min_docs: int = 4, top: int = 150) -> list[dict]:
    """Rank n-grams by how much more often they appear in the focus corpus
    (anchor works and works citing them) than in a baseline social-science sample.
    Weighted log-odds with an informative Dirichlet prior (Monroe, Colaresi & Quinn 2008),
    computed on document frequencies."""
    f = Counter(g for d in focus_docs for g in ngrams(d))
    b = Counter(g for d in baseline_docs for g in ngrams(d))
    nf, nb = max(1, len(focus_docs)), max(1, len(baseline_docs))
    prior = 0.5
    out = []
    for g, cf in f.items():
        if cf < min_docs:
            continue
        cb = b.get(g, 0)
        lf = math.log((cf + prior) / (nf - cf + prior))
        lb = math.log((cb + prior) / (nb - cb + prior))
        var = 1 / (cf + prior) + 1 / (cb + prior)
        z = (lf - lb) / math.sqrt(var)
        out.append({"term": g, "z": round(z, 2), "focus_docs": cf, "baseline_docs": cb,
                    "already_tracked": g in known})
    out.sort(key=lambda r: r["z"], reverse=True)
    # drop n-grams wholly contained in a higher-ranked longer n-gram with similar support
    kept: list[dict] = []
    for r in out:
        if any(r["term"] in k["term"] and k["focus_docs"] >= 0.8 * r["focus_docs"] for k in kept):
            continue
        kept.append(r)
        if len(kept) >= top:
            break
    return kept
