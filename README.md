# Google Ads Competitive Audit Agent

An agentic research tool that helps small local businesses understand why competitors are outperforming them on Google Ads — and what to do about it.

Given a target keyword, business description, and client website URL, the agent uses Bright Data's SERP API to identify who is ranking for that keyword, scrapes those competitor landing pages alongside the client's own site using Bright Data's Web Unlocker, then produces a structured audit report comparing the two across paid search signals.

Built on the [Bright Data deep research agent starter repo](https://github.com/itsajchan/bd-web-research-agents), adapted for local service business competitive intelligence.

## How It Works

1. **SERP search** — Bright Data's SERP API searches Google for the target keyword and returns top organic results
2. **Filter competitors** — the client's own domain is removed from results so only competitor URLs remain
3. **Concurrent scraping** — the client's site and up to 3 competitor pages are fetched in parallel via Bright Data's Web Unlocker, which handles bot detection, CAPTCHAs, and JS rendering; each page is labeled `CLIENT SITE` or `COMPETITOR SITE` in the evidence bundle
4. **Agent synthesis** — the full evidence bundle is handed to a GPT-4.1 agent running on the OpenAI Agents SDK, which synthesizes a structured audit using a custom Pydantic output schema
5. **HTML report** — output is written to a timestamped HTML file in `/output` with a one-click JSON export at the bottom

## Output

- **Summary** — 2-3 sentence executive summary of findings
- **Competitor comparison** — specific side-by-side observations from scraped pages
- **Google Ads recommendations** — prioritized action items (bidding strategy, match types, budget management, Quality Score improvements, flags for negative keyword review)
- **GEO / AI search visibility** — gaps in how the business appears in AI-powered search tools like ChatGPT, Perplexity, and Google AI Overviews
- **Quick wins** — 2-3 things the owner can act on today
- **Open questions** — things requiring manual verification inside Google Ads or Google Business Profile
- **Sources consulted** — all URLs scraped with titles

## Tech Stack

- **OpenAI GPT-4.1** via **OpenAI Agents SDK** (`openai-agents`) — agent orchestration, structured output, tool-calling loop
- **Bright Data SERP API** — keyword-based Google search result discovery
- **Bright Data Web Unlocker** — competitor and client page scraping
- **Pydantic v2** — structured output schema validation
- **Python 3 / asyncio** — concurrent page fetching

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
cp .env.example .env
```

Fill in `.env`:

```
OPENAI_API_KEY=your_key_here
BRIGHT_DATA_API_TOKEN=your_token_here
BRIGHT_DATA_SERP_ZONE=your_serp_zone_name
BRIGHT_DATA_UNLOCKER_ZONE=your_unlocker_zone_name
```

Zone names are found in the Bright Data dashboard under **Proxies & Scraping**.

## Run

```bash
python3 -m bright_research_agent.audit_agent \
  --keyword 'Lake Erie fishing charters' \
  --business 'Walleye fishing charter out of Lorain, Ohio, $1k/month Google Ads budget' \
  --client-url 'https://justwalleye.com'
```

The report is saved to `output/audit_<keyword>_<timestamp>.html`. Open it in any browser.

### Options

```
--max-turns       Max agent loop turns (default: 10)
--log-level       DEBUG, INFO, WARNING (default: INFO)
--openai-timeout  Request timeout in seconds (default: 240)
```

### Troubleshooting timeouts

```bash
python3 -m bright_research_agent.audit_agent \
  --keyword '...' --business '...' --client-url '...' \
  --openai-timeout 300 --max-turns 8
```

## Input Schema

The three CLI arguments are combined internally into a research prompt — the user never writes the prompt directly.

| Argument | Required | Description |
|---|---|---|
| `--keyword` | Yes | The Google search term to analyze (e.g. `"Lake Erie fishing charters"`). Used as the SERP query and as the primary competitive lens for the audit. |
| `--business` | Yes | A short plain-English description of the client's business, including any relevant context like budget or location (e.g. `"Walleye fishing charter out of Lorain, Ohio, $1k/month Google Ads budget"`). Passed to the agent to frame recommendations relative to the client's situation. |
| `--client-url` | Yes | The client's website URL (e.g. `"https://justwalleye.com"`). Fetched directly and separately from the SERP results, labeled `CLIENT SITE` in the evidence bundle. The client domain is also filtered out of SERP results so it doesn't appear as its own competitor. |

### How the evidence bundle is constructed

Before the agent runs, the tool pre-fetches all evidence deterministically:

1. The `--keyword` value is sent to Bright Data's SERP API, which returns the top Google organic results
2. Any result matching the client's domain is filtered out
3. The top 3 remaining competitor URLs are scraped via Web Unlocker and labeled `COMPETITOR SITE`
4. The `--client-url` is scraped separately via Web Unlocker and labeled `CLIENT SITE`
5. The full bundle — SERP metadata, client page content, and competitor page content — is passed to the agent as structured JSON

The agent may make additional Web Unlocker calls mid-loop if it needs more evidence.

---

## Output Schema

The agent returns a structured `AuditReport` object validated by Pydantic. The HTML report renders each field as a section. The raw JSON is also available at the bottom of every report.

### `summary`
**Type:** `string`

2-3 sentence executive summary of the overall audit. Covers what the client is doing well, what the biggest gaps are, and what they should prioritize first.

---

### `competitor_comparison`
**Type:** `list[string]`

Specific side-by-side observations from the scraped pages — what higher-ranking competitors are doing that the client isn't. Each item is a concrete finding, not a generalization (e.g. *"Competitor X displays 47 Google reviews above the fold; the client site shows no reviews on the homepage"*).

---

### `google_ads_recommendations`
**Type:** `list[AdsRecommendation]`

Prioritized action items ordered high → medium → low. Each recommendation has four fields:

| Field | Type | Description |
|---|---|---|
| `priority` | `string` | `high`, `medium`, or `low` — ordered by expected impact for a small-budget advertiser |
| `category` | `string` | One of: `bidding`, `keywords`, `ad_copy`, `landing_page`, `tracking`, `geo_targeting`, `budget` |
| `recommendation` | `string` | The specific action item in plain English, written for a non-expert business owner |
| `reasoning` | `string` | Why this matters, grounded in what was observed on competitor or client pages |

The agent applies these principles when generating recommendations:
- Small budgets ($1k/month or less) require tighter controls — exact match over broad match, manual or enhanced CPC over Smart Bidding until conversion data exists
- Geographic targeting should be set to "Presence only" to avoid wasting budget on out-of-area clicks
- Quality Score improvements (ad copy → keyword → landing page alignment) directly reduce cost-per-click
- Conversion tracking (calls over 60 seconds) is flagged if there's no evidence it's set up
- Google's own optimization suggestions are treated skeptically — they are designed to increase spend, not ROI

---

### `geo_recommendations`
**Type:** `list[string]`

AI search visibility gaps and suggestions based on observable page signals. GEO (Generative Engine Optimization) covers how the business appears in AI-powered tools like ChatGPT, Perplexity, and Google AI Overviews.

Each item flags something the client should check or add, such as:
- Missing `LocalBusiness` schema markup (machine-readable address, hours, phone)
- No FAQ section (high-value for AI citation)
- Vague or unstructured content that AI summarizers tend to skip
- Missing owner/staff bio that would support E-E-A-T signals
- Incomplete Google Business Profile

These are framed as "things to check or add" rather than definitive findings, since full verification requires tools beyond page scraping.

---

### `quick_wins`
**Type:** `list[string]` (exactly 2-3 items)

Things the owner can implement today without technical help. Deliberately short and action-oriented — no jargon, no account access required.

---

### `open_questions`
**Type:** `list[string]`

Things that require manual verification the agent cannot do from page scraping alone. Examples:
- Checking actual search term reports for negative keyword candidates
- Verifying geographic targeting settings ("Presence only" vs "Presence or interest")
- Confirming conversion tracking is set up and recording calls over 60 seconds
- Reviewing ad scheduling to ensure ads only run during business hours
- Checking Google Business Profile completeness and review count

---

### `sources_consulted`
**Type:** `list[SourceURL]`

All URLs scraped during the run, with page titles. Each entry has two fields:

| Field | Type | Description |
|---|---|---|
| `url` | `string` | Full URL of the scraped page |
| `title` | `string` | Page title as returned by Web Unlocker |

Includes both the client site and all competitor sites fetched during evidence collection.

---

## Notes

- Scraped page content is treated as untrusted data — the agent instructions explicitly tell the model not to follow instructions found inside retrieved pages
- Keep `max_sources` low during demos to control cost and latency
- Edit the system prompt at `src/bright_research_agent/audit_instructions.txt` — no code changes needed
- Generated reports are gitignored and stored locally in `/output`
