# Lead Qualifier

A prototype that takes a web-form submission, researches the company, screens it
against a do-not-engage list, scores the fit, writes a row to an Excel tracker and
tells a sales rep on Slack. My solution to the Forward Deployed Engineer take-home.

**How I used AI.** I built it with Claude Code (Claude Opus 5.5), which wrote the code
and the tests. My part was the decisions and the checking: reading the brief and the
client's own website first, settling scope, the form fields and how fit and compliance
should behave, then reviewing each step. I planned in two layers: a few AI models gave
general plans from the brief alone (solid engineering ideas, such as "code under the
AI can only make its verdict stricter"), then research on the client's own site made it specific:
its customers, AWS and Oracle, its real form. Where reviewers agreed I took it, where
they disagreed I decided, and where I could measure, I measured instead of voting. I wrote the expected answers of the evaluation
before running it, and fresh Claude agents that had not seen the build reviewed the
repository three times.

## How it works

```
lead.json
  1 intake       validate; personal data (name, email, title) never goes to the model
  2 research     Claude call 1: the company's website + web search -> cited facts, 3-sentence brief
  3 compliance   Claude call 2: a screening agent on every lead, then a code safety net
  4 scoring      code: size + cloud-spend signal + small bonuses -> fit score -> route
  5 tracker      one row per company in leads.xlsx, in calling order
  6 notify       one Slack message per lead
```

- **A fixed workflow, not a free agent.** Code decides the steps; the screening agent
  gets at most six steps. If the model refuses, runs out of steps or returns something
  broken, the lead is marked "not screened" and goes to a person.
- **The safety net can only make a verdict stricter.** An exact competitor name or a
  known competitor domain is always a match, whatever the agent says, so text planted
  on a website cannot clear it.
- **No fact without a source.** A quote must be on a page the program fetched, and a
  web-search fact must cite a URL the search really returned; otherwise it is "unknown".
- **Two separate outputs.** The route — `SALES-READY`, `CHECK-FIRST` (one question for
  a person: possible competitor, unknown headquarters, unreachable site) or
  `DO-NOT-ENGAGE` — depends only on the compliance check, never on the score. The fit
  score only sets the order in which sales calls the leads.

## Run it

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```
uv sync
cp .env.example .env          # Windows: copy; then fill in ANTHROPIC_API_KEY and SLACK_WEBHOOK_URL
uv run leadqual run leads/01_good_fit.json            # add --no-notify to skip Slack
uv run leadqual eval          # 26 labelled compliance cases against the live model
uv run pytest                 # 307 unit tests; no network, no API key
```

## Proof: four leads, run live

| Lead (`leads/`) | Route | Fit | Why |
|---|---|---|---|
| `01_good_fit` Bitrise | SALES-READY | 100 | **clean good fit**: 51-1000 employees, high cloud workload, runs on AWS |
| `02_borderline` Rába | SALES-READY | 45 | **borderline**: large company, low cloud workload; callable, mid-list |
| `04_near_match` "ClowdTrim Analytics" | CHECK-FIRST | 45 | possible competitor (CloudTrim Inc): a person checks first |
| `03_flagged_competitor` "Cloud Trim Inc." | DO-NOT-ENGAGE | 100 | **flagged**: competitor. A perfect fit on paper; compliance outranks fit |

Bitrise and Rába are real companies used only as research targets (they did not apply;
contacts are invented). The two flagged leads are fictional, on `.test` domains. The
sheet is `demo/leads.xlsx`; the full result of each run is in `demo/runs/`.

![The Excel tracker](demo/tracker.png)

![Slack: the two sales-ready leads](demo/slack-1-sales-ready.png)

![Slack: the blocked competitor and the near-match](demo/slack-2-flagged.png)

## Data sources

The company's own website (home page plus up to four sub-pages such as about, imprint,
careers) and Claude's web search for what sites often omit: headquarters and headcount.
Free, the company describing itself, and checkable. No paid enrichment API: for a
prototype it is a second vendor for facts these two usually give. The form gets six
optional fields (job title, country, size band, monthly cloud bill band, cloud
providers, message), each asking something research cannot find.

## Fit

Two axes from the brief, 0-50 points each, in `config.toml` so a sales manager can
change them without touching a prompt. Same facts, same score.

**Company size** (total employees, from the form or from research)

| Employees | Points |
|---|---|
| 1-10 | 15 |
| 11-50 | 40 |
| 51-1000 | 50 |
| 1001-5000 | 35 |
| 5000+ | 25 |

**Cloud-spend signal.** A cloud bill cannot be seen from outside, so one of these
counts: the monthly bill the lead declared on the form, or, if there is none, the
workload estimated from the website.

| Signal | Points |
|---|---|
| declared bill over $20k/month | 50 |
| declared bill $5k-20k | 35 |
| declared bill under $5k | 10 |
| workload estimated from the website: high | 40 |
| workload estimated from the website: medium | 25 |
| workload estimated from the website: low | 5 |

If the declared bill and the website clearly disagree (a bill over $20k, a low
workload), the lead goes to a person. Unknown is not small: an axis with no data gets a
neutral 25 and the row says so. Small bonuses (max 10): runs on AWS or Oracle (the two
services on the client's cost-optimisation page; other providers lose nothing), a
decision-maker title, a stated cloud-cost problem. The curve is a hypothesis to tune on
deals that actually closed.

## Compliance and near-matches

| Lead | Verdict |
|---|---|
| "Cloud Trim Inc." (spelling, spacing, legal suffix) | match -> do not engage |
| "Nimbus Savings (formerly CloudTrim)", "a SpendWise Cloud company" | match -> do not engage |
| "ClowdTrim Analytics": similar name and business, no proof it is the same | possible match -> check first |
| "Right Size Shoes": similar name, unrelated business | clear, near-match noted |
| sells cloud cost optimisation but is not on the list | possible match -> check first |

A similar name proves nothing, so names are not scored by similarity: the agent reads
the website and judges what the company does. Sanctions: only the headquarters counts.
The country typed into the form can only count against a lead, never clear it; if no
source confirms the headquarters, a person checks.

**Measured:** 26 fictional leads with expected answers written before any run
(`eval/`), including three prompt-injection attempts: Claude Sonnet 5.5 got 26/26, one
of them decided by the code safety net. Opus 5.5 and Sonnet both got the first 24 right;
Sonnet in less than half the time, so Sonnet runs it.

## Assumptions, and what would change in production

- Thresholds, size bands and the sanctions table are my assumptions, all in `config.toml`;
  the sanctions table is a simplified prototype policy, not legal advice.
- Self-reported answers count but are labelled "self-reported, unverified".
- One lead per run, synchronous; a real lead with web search takes about 20-30 seconds.
- Not built, on purpose: **RAG** (the lists fit in the prompt; this is name matching,
  not retrieval), an **MCP server** (one program calls its own two tools; it becomes
  useful if sales want to ask Claude about a lead), a cheaper first-pass model (measured
  in `experiments/`: 34/36, left out because web search is where the cost is).
- In production: a named-entity sanctions list with fuzzy, transliteration-aware
  matching; an ownership check in a company register (e.g. GLEIF parent links); a queue
  instead of one lead per run; fit weights calibrated on closed deals.
