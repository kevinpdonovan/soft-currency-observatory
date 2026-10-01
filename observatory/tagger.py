"""Optional Claude pass: relevance 0-3, scholarly substance 0-2, and a one-line note.

Runs only if ANTHROPIC_API_KEY is set; otherwise density alone ranks the review.
The screener sees titles, venues, abstracts, and which anchors a work cites.
"""
from __future__ import annotations

import json
import re

from .profile import env

BATCH = 25

SYSTEM = """You screen candidate scholarly works for a monthly research archive and return strict JSON.
Be discriminating. Relevance scale:
3 = directly about soft currencies as the project understands them (see brief): their history,
    instruments and expertise, livelihoods, meanings, or place in the currency hierarchy;
2 = clearly useful to the project (adjacent monetary or financial scholarship with a historical,
    institutional or ethnographic argument);
1 = marginal (cites the literature in passing; generic macro or finance);
0 = irrelevant.
Citing an anchor work is evidence of fit but not proof: judge the work itself.
"quality" 0-2 for scholarly substance judged from title, abstract and venue: 2 = substantive
scholarship with a clear argument or evidence; 1 = acceptable (thin abstract, working paper,
review); 0 = poor (formulaic, incoherent, predatory-looking, or not scholarship).
The note (max 25 words) says concretely what the work contributes. Do not repeat the title.
Do not speculate beyond the text. Write in English."""


def _prompt(profile: dict, batch: list[dict]) -> str:
    rows = "\n".join(json.dumps({
        "id": it["id"], "title": it["title"], "venue": it.get("venue") or it.get("source", ""),
        "year": it.get("year"), "type": it.get("work_type", ""), "cites_anchors": it.get("anchors", []),
        "terms": it.get("terms", []), "abstract": (it.get("summary") or "")[:900]}, ensure_ascii=False)
        for it in batch)
    return (f"PROJECT BRIEF\n{profile['project']['brief']}\n\nWORKS (one JSON object per line)\n{rows}\n\n"
            'Return ONLY a JSON array, same order: [{"id": "...", "relevance": 0-3, "quality": 0-2, "note": "..."}]')


def tag_items(profile: dict, items: list[dict], log=print) -> bool:
    key = env("ANTHROPIC_API_KEY")
    if not key or not items:
        return False
    import anthropic

    client = anthropic.Anthropic(api_key=key)
    model = env("TAGGER_MODEL", "claude-haiku-4-5")
    by_id = {it["id"]: it for it in items}
    for i in range(0, len(items), BATCH):
        batch = items[i:i + BATCH]
        try:
            msg = client.messages.create(model=model, max_tokens=4000, system=SYSTEM,
                                         messages=[{"role": "user", "content": _prompt(profile, batch)}])
            text = "".join(b.text for b in msg.content if b.type == "text")
            m = re.search(r"\[.*\]", text, re.S)
            results = json.loads(m.group(0)) if m else []
        except Exception as ex:
            log(f"  screener batch {i // BATCH + 1} failed: {ex}")
            continue
        for r in results:
            it = by_id.get(r.get("id"))
            if it:
                it["ai"] = {"relevance": int(r.get("relevance", 0)), "quality": int(r.get("quality", 1)),
                            "note": (r.get("note") or "").strip()[:240]}
    return True
