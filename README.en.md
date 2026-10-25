# Daily arXiv Digest

[中文](README.md) · [**English**](README.en.md)

Turn one arXiv category feed into a daily research brief you can actually finish in **20 minutes** — and that makes a judgement for you.

Every day it fetches new papers and field news → extracts facts → scores and comments against your reader profile → renders an email (or a standalone HTML file).

`v0.11.1` · Python ≥ 3.11 · MIT

---

## 1. What this is

Most people subscribe to arXiv like this: a pile of titles arrives every morning, and none of them get opened. The problem is not a lack of information — it is **decision paralysis caused by too much of it**. Of the dozens of new submissions, what you actually need is: *which two or three are relevant to me, why, and is it worth reading right now.*

So this is not a summary relay. It does four things:

1. **Filter** — score against domain packs and your profile; 14 papers make the email, the rest stay out of your way.
2. **Extract** — pull *what data was used, how the sample was cut, how systematics were corrected, what method, what result* out of the full PDF, with a verbatim quote behind every field.
3. **Comment** — write only on top of those extracted facts, give a reading recommendation, and check the paper against the field's usual failure modes.
4. **Give you the wider picture** — what other fields are doing this week, what the community is arguing about, which data releases are coming.

---

## 2. What arrives each day

The email runs "the world first, then you":

| Section | Content | Notes |
| --- | --- | --- |
| **LLM overview** | 3–5 sentence lead | The one thing worth noticing today |
| **This week's pulse** | 3–4 threads the week converged on | Every claim must cite an arXiv ID; unsupported claims are dropped |
| **Neighbourhood radar** | 1 item from each of 5 other fields | Exoplanets / stars / high-energy / instrumentation / solar system; the rest collapsed |
| **Calendar** | Data releases, conferences, deadlines | `D-19` countdown; undated items show "expected May" |
| **Deep read** | 4 papers × 10 blocks | See below |
| **Skim** | 10 papers, one row each | One Chinese sentence + expandable bilingual abstract |
| **The field** | Second-hand write-ups from 9 sources | Chinese title + 150–220 character Chinese digest + source badge |
| **Submission volume** | This week vs last week, by field | Only verifiable counts, no invented "buzz" |

**The 10 blocks of a deep-read card** (the centre of gravity of this project):

1. One-line verdict + reading advice (read it today / method and figures only / archive it)
2. Method chain: a left-to-right skeleton `data → sample cut → estimator → inference`
3. Seven questions: data, processing, systematics, method, result, caveats, relevance to you
4. Key numbers table: sample size, uncertainties, significance — each with a source quote
5. Figure pointers: captions pulled from the PDF by regex (costs no model tokens)
6. Three concrete things you can do today
7. Red-flag audit: the field's usual pitfalls, one by one (discussed / partly / not mentioned)
8. Glossary: only terms outside your profile's vocabulary
9. Chinese abstract
10. English abstract (collapsed, click to expand)

---

## 3. Three design decisions

**① Two stages, not one generation.**
A cheap model first extracts structured facts from the full text (each field carries a `quote`); a stronger model then writes the commentary from those facts plus your profile. Benefits: a bad extraction is traceable to a field, commentary cannot invent numbers, facts can be cached, and swapping the commentary model does not require re-extraction.

**② Every claim carries evidence.**
"This paper said X" in the pulse section must come with an arXiv ID; if it cannot, the claim is dropped. Silence beats a confident sentence with nothing behind it.

**③ Domain knowledge lives in YAML, not in Python.**
`config/packs/*.yaml` defines categories, review focus, common red flags, and pulse topics. Changing fields means editing YAML.

---

## 4. Quick start (5 minutes)

```bash
git clone https://github.com/Supercell023/arxiv-astro-digest.git
cd arxiv-astro-digest

python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt        # Windows
# source .venv/bin/activate && pip install -r requirements.txt     # macOS / Linux

cp .env.example .env        # Windows: copy .env.example .env
```

Minimal `.env`:

```ini
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.deepseek.com     # point anywhere OpenAI-compatible
OPENAI_MODEL=deepseek-flash
ANALYSIS_ENABLED=true                        # defaults to false: off means no deep read
```

Then:

```bash
python -m arxiv_digest.selfcheck                 # 22 offline checks, no network, no cost
python -m arxiv_digest.main --out preview.html   # real run, HTML only, no email sent
```

Add the five SMTP variables when you are happy, drop `--out`, and it starts mailing.

> Behind the GFW? Add `-i https://pypi.tuna.tsinghua.edu.cn/simple` to pip.

---

## 5. Common commands

| Command | Purpose |
| --- | --- |
| `python -m arxiv_digest.selfcheck` | 22 offline checks (imports, config, parsing, dispatch, rendering, themes…) |
| `python -m arxiv_digest.main --out preview.html` | Real run; writes HTML, sends nothing, writes no state |
| `python -m arxiv_digest.main` | Full run: sends the email and writes `state.json` |
| `python scripts\build_preview.py` | Render from recorded data (offline, free) |
| `python scripts\build_preview.py --theme light` | Preview the light theme |
| `python scripts\preview_digest.py --out p.html` | Real data, HTML only |
| `python scripts\feedback.py record --id 2610.31660 --vote up` | Record feedback |
| `python scripts\feedback.py stats` | Inspect learned preferences |
| `python scripts\eval_analysis.py check --digest dump.json` | Measure extraction quality |
| `python scripts\refresh_pulse.py` | Re-fetch pulse material |

---

## 6. Configuration

All 57 variables are documented in [`.env.example`](.env.example). The ones you will actually touch:

| Variable | Default | Purpose |
| --- | --- | --- |
| `DEEP_PAPERS` / `BRIEF_PAPERS` | 4 / 10 | Deep-read and skim counts (the 20-minute budget) |
| `ANALYSIS_ENABLED` | false | Enable the two-stage deep read |
| `FULLTEXT_ENABLED` | true | Download the PDF for deep-read papers |
| `EMAIL_THEME` | dark | `dark` deep-space / `light` warm paper |
| `EMAIL_ATTACH_HTML` | true | Attach a forwardable HTML file |
| `CALENDAR_ENABLED` | true | Calendar section |
| `RADAR_ENABLED` | true | Neighbourhood radar section |
| `PULSE_ENABLED` | true | Weekly pulse section |
| `TRANSLATE_ENABLED` | true | Chinese translation of second-hand items |
| `FIELDS` | empty | Comma-separated domain pack ids; empty = all enabled |

**Model settings**: `OPENAI_BASE_URL` / `OPENAI_MODEL` / `ANALYSIS_EXTRACT_MODEL` / `ANALYSIS_ASSESS_MODEL` / `PULSE_MODEL`.
DeepSeek enables thinking mode by default; six `*_THINKING` switches control it per call site (unset = follow the model default; DeepSeek is auto-disabled).

**Cost**: one full run is ~60k input + ~15k output tokens. On DeepSeek Flash that is about **$0.04/day** with thinking off and **$0.10/day** with it on.

---

## 7. Making it yours

No code changes — three files:

| File | Purpose |
| --- | --- |
| `config/profile.yaml` | Reader profile: who you are, what you care about, minutes per day, what the seven questions should ask |
| `config/packs/*.yaml` | Domain pack: categories, review focus, red flags, pulse topics |
| `config/sources.yaml` | Second-hand sources and conferences (Indico event ids) |

Three packs ship by default: `galaxy_cosmology`, `galaxy_formation`, `generic` (fallback).
A new field is a copied YAML with new content, then its `id` added to `FIELDS`.

---

## 8. Upgrade notes

### Version history

| Version | Theme |
| --- | --- |
| v0.1 | Baseline: fetch → score → Chinese summary → email |
| v0.2 | Multi-source (RSS/Indico) + cross-day state + CI self-check |
| v0.3 | Weekly pulse (count first, judge second, cite always) |
| v0.4 | Neighbourhood radar / translation / feedback loop / eval set |
| v0.5 | Collapsible sections, rebuilt "the field", typography pass |
| v0.6 | LaTeX → Unicode, per-source cadence (daily/weekly), serif fonts |
| v0.7 | Layout rebuild: journal masthead, section eyebrows, semantic palette |
| v0.8 | Unified model layer `llm_client`, DeepSeek protocol support |
| v0.9 | Thinking mode made explicit, batched translation |
| v0.10 | HTML attachment / thicker field notes / per-section colours |
| v0.11 | Deep-space dark theme + tokenised palettes (`EMAIL_THEME`) |

Full details in [CHANGELOG.md](CHANGELOG.md).

### Upgrading from an older copy

1. **Diff your `.env`.** New variables all have defaults, but write `EMAIL_THEME`, `FULLTEXT_*`, `RADAR_*`, `TRANSLATE_*` explicitly so you do not think a feature is on when it is not.
2. **Keep `.env` comments on their own line.** `KEY=   # comment` is parsed by dotenv as the literal value `# comment`. The code now strips it, but old files are worth cleaning.
3. **`state.json` is compatible.** Since v0.6 it carries a `fetched` map for per-source cadence; missing keys are filled in automatically.
4. **`deepseek-chat` / `deepseek-reasoner` are retired.** Current names are `deepseek-flash` and `deepseek-v4-pro`.
5. **Run the self-check after upgrading** — 22 green before a real run.

---

## 9. Layout

```
arxiv_digest/
  main.py           Entry point: SMTP preflight, --out mode, sending
  pipeline.py       Main flow: fetch → analyse → pulse/radar/translate → render
  config.py         57 env vars → Config
  arxiv_client.py   arXiv API (paging, 429 backoff, adaptive lookback window)
  analysis.py       Two stages: extract facts → write commentary
  llm_client.py     Unified model calls (JSON fallback, thinking switch, truncation retry)
  fulltext.py       PDF download, extraction, cleaning, condensing, caption mining
  pulse.py          Weekly pulse: topic counts + evidence-constrained judgement
  radar.py          Neighbourhood radar: cross-field sampling and collapsing
  translate.py      Batched Chinese translation of second-hand items
  feedback.py       Feedback learning (category / author weights)
  calendar.py       Countdown to releases and conferences
  packs.py          Domain pack loading and dispatch
  profile.py        Reader profile
  mathfmt.py        LaTeX → Unicode
  theme.py          Two 54-key palettes
  state.py          Cross-day dedupe, source cadence, submission counts
  sources/          RSS and Indico fetchers
  templates/        Email HTML (no hard-coded colours)
  selfcheck.py      22 offline checks
config/             Profile, packs, sources, calendar, radar, replay data
scripts/            Preview, evaluation, feedback, pulse refresh
docs/               PRODUCT_PLAN.md (design doc), PROJECT_REVIEW.md (early audit)
```

---

## 10. Data boundaries

**Reliably available**: arXiv API (rate-limit friendly, with backoff), AAS Nova, NASA, Phys.org, Universe Today, ESA, ESO, DESI, Keck, Indico event exports.

**Not available / deliberately out of scope**:

| Item | Reason |
| --- | --- |
| Astrobites | Returns 403 to datacentre IPs (usually fine on GitHub Actions) |
| hubblesite / webbtelescope / Chandra / NOIRLab | No public RSS (404) |
| Real-time gravitational-wave alerts | Needs an always-on listener; Actions cannot do it |
| Public paper discussion | Needs a backend and moderation |
| Citation graphs | Needs an offline index |

---

## 11. Known limitations

- **Scanned PDFs** yield no text, so analysis falls back to the abstract (the card is marked "abstract only").
- **Submission volume** needs two consecutive days of state; the first run shows nothing.
- **Collapsing** relies on `<details>`: Apple Mail and iOS fold it, Gmail and Outlook expand it inline (acceptable — nothing is lost).
- **Grid texture and card glow** are ignored by Outlook, which falls back to flat colours; readability is unaffected.
- **There is no automatic check that a summary is faithful to the source** — only quotable evidence and a weekly manual spot-check.

---

## 12. Docs and licence

- [docs/PRODUCT_PLAN.md](docs/PRODUCT_PLAN.md) — design document (why it works this way, what broke in each version)
- [docs/PROJECT_REVIEW.md](docs/PROJECT_REVIEW.md) — early project audit and scoring diagnosis
- [CHANGELOG.md](CHANGELOG.md) — change log
- [MIT License](LICENSE)
