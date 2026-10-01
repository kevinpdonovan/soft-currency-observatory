# Soft Currency Observatory

The website and monthly literature sweep for the Soft Currency research network. It is built on
the Insubordinate Finance Terminal but organised around **citations** rather than keywords: a set
of *anchor works* defines the field, and the sweep follows who cites them.

```
 1st of the month (auto)            You (≈20 min)                    Minutes later
┌────────────────────────────┐     ┌──────────────────────────┐     ┌──────────────────────┐
│ Works citing the anchors   │     │ Review issue on GitHub   │     │ Site + RSS update:   │
│ (OpenAlex · Semantic       │ ──▶ │ untick the noise, ★ to   │ ──▶ │ monthly accessions,  │
│ Scholar · Scholar alerts)  │     │ feature, tick candidate  │     │ archive, density,    │
│ + keyword/journal/author   │     │ anchors, close the issue │     │ lexicon counts       │
│ → corpus → density → review│     └──────────────────────────┘     └──────────────────────┘
└────────────────────────────┘
```

## How it works

1. **Anchors** (`config/anchors.yaml`). About 30 works that define the field. Each gets a
   *weight* that falls as its total citation count rises. Being cited alongside Guyer's
   *Marginal Gains* says more about a new work than being cited alongside Parry & Bloch.
2. **Citing works.** Every month, three indexes are checked for works that cite an anchor:
   - OpenAlex: the main source; looks back 18 months, because citation links are often added late
   - Semantic Scholar: a second index, stronger on some books and humanities venues
   - Google Scholar "new citations" alerts, sent by email: the best coverage of monographs
3. **Related works.** Keyword searches (social sciences, economics and humanities only), plus
   watched journals and watched authors. These need at least two tracked terms to get through.
4. **The corpus** (`data/corpus/works.jsonl`). Every work ever seen, one line each: the anchors
   it cites, the tracked terms it uses, its density and its status. Works the review doesn't
   publish stay in the corpus and still count on the Density page.
5. **Density** = weights of the anchors cited + 0.25 per tracked term (up to 8) + a small bonus
   for watched venues.
6. **Trust** (no h-index): watched journals and authors; trusted publishers and university
   presses; DOAJ-listed open-access journals; deny lists for predatory venues. An unknown
   venue is pre-ticked only when the work cites two or more anchors.
7. **Candidate anchors.** Works that many corpus works cite, but that aren't anchors yet, are
   proposed in each review. Ticking one adds it to `anchors.yaml`.
8. **Screening.** Claude rates relevance (0–3) and substance (0–2) and drafts a one-line note.
   Optional: without an API key, density alone ranks the review.

## The files you edit

| File | What it controls |
|---|---|
| `config/anchors.yaml` | The anchor works (with optional manual `weight`) |
| `config/anchors_lock.yaml` | Written on the first run: what each anchor matched in OpenAlex. **Check entries marked `check: true`** |
| `config/observatory.yaml` | The screening brief, tracked terms, negative keywords, keyword searches, watched journals and authors, trust lists, scoring |
| `config/sources.yaml` | Which indexes are swept and how far back |
| `config/content.yaml` | Text for Home, Research, Seminar (add sessions here), Join and About; members |
| `config/readings.yaml` | Core Readings (separate from the anchors) |
| `config/lexicon.yaml` | Lexicon entries; `tracks` links each one to a term, so it shows counts |
| `config/site.yaml` | Title, base URL, contact email, join link, library proxies |

Edit on GitHub (pencil icon → *Commit changes*). The site rebuilds within minutes; sweep
changes take effect at the next run.

## One-time setup (about 30 minutes)

1. **Create a public repository** `soft-currency-observatory` and upload these files
   (or give Claude a fine-grained token with *Contents*, *Issues*, *Workflows* and *Pages*
   permissions on that repository, and it can push them).
2. **Turn on Pages:** *Settings → Pages → Source: GitHub Actions*.
3. **Add secrets and variables:** *Settings → Secrets and variables → Actions*
   - Secret `ANTHROPIC_API_KEY`: optional, for screening and notes; costs about $1–3 a month
   - Variable `REVIEWER`: your GitHub username
   - Variable `CONTACT_EMAIL`: sent politely to OpenAlex
   - Optional secrets: `OPENALEX_API_KEY`, `SEMANTIC_SCHOLAR_API_KEY` (free on request; each raises that index's rate limit)
4. **Match the anchors:** *Actions → Tools → tool: resolve*. Then open `config/anchors_lock.yaml`
   and check every entry marked `check: true`, especially books. OpenAlex sometimes splits one
   book into several records; the others are kept under `also`.
5. **Founding backfill:** *Actions → Founding backfill → Run workflow*. This collects every
   existing work citing the anchors, which can take an hour or more, and opens the *Founding
   review* issue. Review it and close it. The site then goes live.
6. **Tune the vocabulary:** *Actions → Tools → tool: terms*. This writes
   `data/terms_suggested.yaml`: terms that are much more common in the anchor literature than in
   social science at large. Copy the good ones into `observatory.yaml → terms`.
7. Set `base_url` in `config/site.yaml` to the Pages address.

From then on, the harvest runs automatically on the 1st of every month.

## Google Scholar citation alerts (recommended for books)

1. Use a dedicated Gmail account (e.g. `softcurrency.observatory@gmail.com`). Turn on
   2-Step Verification and create an *App password*.
2. Add secrets `IMAP_USER` (the address) and `IMAP_PASSWORD` (the app password).
3. Signed in to Google Scholar as that account, find each anchor book, click *Cited by*, then
   the envelope icon (*Create alert*). The anchor is recognised from the alert's subject line.

## Citation lineage (side tool)

*Actions → Tools → tool: lineage, target: guyer-2004* (any anchor key or OpenAlex `W…` id).
The tool lists every work citing the target and ranks them by their own citations, overall and
per year. It also names the **carriers**: authors whose citing work is cited most in turn. And
it shows which fields cite the target, and which fields cite its most-cited descendants. The
result appears as a page linked from Density, plus `data/lineage/<key>.md`.

## For developers

```
pip install -r requirements.txt
python -m observatory resolve | backfill | harvest | build | terms | lineage KEY
python -m observatory publish REVIEW_BODY.md
python tests/test_pipeline.py      # offline end-to-end test (network faked)
```
