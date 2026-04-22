# BPO Sales Ops Pipeline

An AI-powered sales operations pipeline that automatically processes inbound BPO partner emails, generates comprehensive sales artifacts, and delivers them through a human-in-the-loop approval workflow. Includes a real-time dashboard with Google Sheet sync and deliverable tracking.

```
  +-----------+      +----------------+      +------------------+      +-----------+
  |   Gmail   | ---> |  FastAPI + DAG | ---> |  Slack Approval  | ---> |  Google   |
  |   Inbox   |      |  (12 Modules)  |      |  (Approve/Reject)|      |  Drive    |
  +-----------+      +----------------+      +------------------+      +-----------+
       |                    |                        |                       |
   BPO emails          Claude AI              Human review             Artifacts
   auto-polled       generates docs          via Slack buttons       auto-delivered
                            |
                    +-------v--------+
                    |   Dashboard    |
                    | (Lovable App)  |
                    | Pipeline view  |
                    | Stale alerts   |
                    | Deliverable    |
                    |   tracking     |
                    +----------------+
```

---

## Table of Contents

- [How It Works](#how-it-works)
- [Architecture](#architecture)
- [Pipeline DAG](#pipeline-dag)
- [Module Reference](#module-reference)
- [API Reference](#api-reference)
- [Dashboard & Deliverable Tracking](#dashboard--deliverable-tracking)
- [Session Lifecycle](#session-lifecycle)
- [Project Structure](#project-structure)
- [External Services](#external-services)
- [Setup & Installation](#setup--installation)
- [Configuration](#configuration)
- [Deployment](#deployment)
- [Running](#running)
- [Testing](#testing)
- [BPO Partner Registry](#bpo-partner-registry)

---

## How It Works

A BPO partner (e.g., ResultsCX, Startek) sends an email requesting sales materials for a prospect company. The pipeline automatically:

1. **Detects** the email via Gmail polling (or manual API submission)
2. **Classifies** the sender, extracts the target company, and determines what deliverables are needed
3. **Generates** a full suite of AI-powered sales artifacts in parallel
4. **Posts** an approval request to Slack with Approve/Reject buttons
5. **Delivers** approved artifacts to Google Drive, updates the pipeline sheet, drafts a reply email, and posts a Slack summary
6. **Syncs** pipeline state to a Postgres-backed dashboard in real time via Google Drive push notifications

```
 INBOUND EMAIL                         PHASE 1                              GATE                    PHASE 2
 from BPO partner                  (Classification &                   (Human Review)            (Delivery)
                                    Content Generation)

 +------------------+     +-------------+     +------------------+     +----------+     +------------------+
 | jordan@resultscx |     |             |     | brand_extractor  |     |          |     | drive_manager    |
 | .com             |---->| classifier  |---->| demo_generator   |     |  Slack   |     |   (upload all)   |
 |                  |     |             |     | deep_research    |---->| Approve/ |---->| pipeline_tracker |
 | "Prepare package |     | - match BPO |     | openai_research  |     |  Reject  |     | email_composer   |
 |  for GameStop"   |     | - extract   |     | stakeholder_intel|     |  Buttons |     | slack_summary    |
 |                  |     |   company   |     | cx_intel         |     |          |     +------------------+
 +------------------+     | - parse     |     +--------+---------+     +----------+
                          |   intent    |              |
                          +-------------+     +--------v---------+
                                              | deck_generator   |
                                              | (needs all above)|
                                              +------------------+
```

---

## Architecture

```
bpo-sales-ops/
 |
 |-- orchestrator/              Core engine
 |   |-- main.py                FastAPI app, 22 endpoints, lifespan, Slack handler,
 |   |                          Gmail poller, sheet watch registration, webhook receiver
 |   |-- dag.py                 DAG runner with dependency resolution
 |   |-- session.py             PostgreSQL session persistence
 |   |-- deliverable_tracker.py Snapshot-based pipeline tracking + stale detection
 |   |-- config.py              Pydantic settings (env vars)
 |   +-- registry.py            Module registration system
 |
 |-- modules/                   12 pipeline modules
 |   |-- classifier/            Email classification + BPO matching
 |   |-- brand_extractor/       Brand.dev API + color palette generation
 |   |-- demo_generator/        Demo link lookup
 |   |-- deep_research/         Claude Opus + web search -> branded PDF report
 |   |-- openai_research/       OpenAI GPT research (alternative to Claude deep research)
 |   |-- stakeholder_intel/     Claude Opus + web search -> branded PDF brief
 |   |-- cx_intel/              Review scraping -> professional .xlsx + branded .pdf
 |   |-- deck_generator/        AI slide content -> shadcn-styled .pptx pitch deck
 |   |-- drive_manager/         Google Drive upload + folder management
 |   |-- pipeline_tracker/      Google Sheets + Postgres pipeline state
 |   |-- email_composer/        AI email draft -> Gmail draft creation
 |   +-- slack_manager/         Block Kit summary message
 |
 |-- modules/report_templates/  Jinja2 HTML templates for PDF generation
 |   |-- base.html              Shared CSS design system (stone palette, stat cards, callouts)
 |   |-- cx_intel.html          CX intelligence report (10 sections)
 |   |-- deep_research.html     Company deep dive (TOC, callouts, sources)
 |   |-- stakeholder_intel.html Stakeholder brief (career arc, psych profile, playbook)
 |   |-- renderer.py            Jinja2 environment + custom filters
 |   +-- styles.css             Shared stylesheet
 |
 |-- shared/                    Cross-cutting utilities
 |   |-- anthropic_client.py    Claude Sonnet + Opus wrappers
 |   |-- google_auth.py         OAuth token refresh with caching
 |   |-- storage.py             Artifact file paths
 |   +-- types.py               Pydantic models (SessionContext, Artifact, etc.)
 |
 |-- gmail_poller/              Background email ingestion
 |   +-- poller.py              Gmail API polling with dedup + skip filters
 |
 |-- config/
 |   +-- bpo_registry.json      6 BPO partner definitions
 |
 |-- infra/
 |   |-- Dockerfile             Python 3.12-slim, port 8080
 |   +-- docker-compose.yml     App + Postgres 16
 |
 |-- railway.toml               Railway deployment config
 |-- .dockerignore              Docker build exclusions
 |
 +-- tests/                     116 tests, all passing
     |-- conftest.py            Shared fixtures + MockAsyncClient
     |-- mocks.py               Mock API responses
     |-- test_api.py            Endpoint tests (incl. webhook, tracker, dashboard)
     |-- test_classifier.py     22 classification tests
     |-- test_dag.py            DAG execution tests
     |-- test_e2e.py            Full pipeline tests
     |-- test_gmail_poller.py   14 poller tests
     +-- test_modules.py        Module unit tests (all 12 modules)
```

---

## Pipeline DAG

The pipeline is a directed acyclic graph executed in two phases with automatic dependency resolution and parallel batch execution.

### Phase 1 — Classification & Content Generation

```
                              +------------------+
                              |    classifier    |  Batch 0 (solo)
                              |  Match BPO domain|
                              |  Extract company |
                              |  Parse intent    |
                              +--------+---------+
                                       |
            +-----------+-----------+--+--+-----------+-----------+-----------+
            |           |           |     |           |           |           |
            v           v           v     v           v           v           v
  +---------+--+ +------+-----+ +--+--+ +-+--------+ +-+-------+ +---------+  Batch 1
  |   brand    | |    deep    | |demo | |stakeholder| |cx_intel | | openai  |  (parallel)
  |  extractor | |  research  | |gen  | |  intel    | |         | | research|
  |            | |            | |     | |           | |         | |         |
  | Brand.dev  | | Opus +     | | DB  | | Opus +    | | Sonnet +| | GPT    |
  | API + HSL  | | web search | | lkp | | web srch  | | scraper | | search |
  | palette    | | -> PDF     | |     | | -> PDF    | | -> xlsx | |        |
  +-----+------+ +-----+------+ +--+--+ +-----+-----+ +---+----+ +--------+
        |               |          |           |            |
        +-------+-------+----------+-----------+------------+
                |
                v
       +--------+---------+
       |  deck_generator  |  Batch 2 (waits for all above)
       |                  |
       | Sonnet generates |
       | slide JSON ->    |
       | python-pptx      |
       | -> .pptx         |
       +------------------+

  Status: received -> classifying -> awaiting_approval
  Artifacts: .pdf (x3)  .xlsx  .pptx  .json (brand guide)
```

### Human Approval Gate

```
  +----------------------------------------------------------------+
  |                    SLACK APPROVAL MESSAGE                        |
  |                                                                 |
  |  New BPO Package: GameStop | ResultsCX                         |
  |                                                                 |
  |  Company:      GameStop                                        |
  |  BPO Partner:  ResultsCX                                       |
  |  Deliverables: demo, deep_research, stakeholder_intel,         |
  |                cx_intel, pitch_deck                             |
  |  Session:      sess_a1b2c3d4                                   |
  |                                                                 |
  |  Artifacts Generated:                                          |
  |  * gamestop_Deep_Research.pdf (deep_research)                  |
  |  * gamestop_jane-smith_Stakeholder_Intel.pdf (stakeholder)     |
  |  * gamestop_cx_intel.xlsx (cx_intel_xlsx)                      |
  |  * gamestop_cx_intel.pdf (cx_intel_pdf)                        |
  |  * gamestop_pitch_deck.pptx (pitch_deck)                      |
  |                                                                 |
  |  [  Approve & Deliver  ]    [  Reject  ]                       |
  +----------------------------------------------------------------+
```

### Phase 2 — Delivery

```
       +------------------+
       |  drive_manager   |  Batch 0
       |                  |
       | Upload all       |
       | artifacts to     |
       | Google Drive     |
       | (per-company     |
       |  subfolder)      |
       +--------+---------+
                |
       +--------+--------+
       |                  |
       v                  v
  +----+--------+  +------+------+
  |  pipeline   |  |   email     |  Batch 1 (parallel)
  |  tracker    |  |  composer   |
  |             |  |             |
  | Update      |  | Sonnet ->   |
  | Google      |  | HTML email  |
  | Sheet +     |  | -> Gmail    |
  | Postgres    |  | draft       |
  +----+--------+  +------+------+
       |                  |
       +--------+---------+
                |
                v
       +--------+---------+
       | slack_summary    |  Batch 2
       |                  |
       | Post delivery    |
       | summary with     |
       | Drive links      |
       +------------------+

  Status: approved -> delivering -> complete
```

---

## Module Reference

### Phase 1 Modules

| Module | AI Model | Output | Description |
|--------|----------|--------|-------------|
| **classifier** | Claude Sonnet | ctx fields | Matches sender domain to BPO registry, calls Sonnet to extract company name, deliverables, contact info, pain points |
| **brand_extractor** | -- | .json | Calls Brand.dev API for company colors/logos/fonts, generates HSL-derived deck palette, falls back to defaults on failure |
| **demo_generator** | -- | metadata | Looks up existing demo link in Postgres; if not found, flags `action_required: email_demo_address` |
| **deep_research** | Claude Opus + web search + thinking | .pdf | Extended thinking (10k budget) + web search produces a multi-section report, rendered as branded PDF with cover page, TOC, callout boxes, and source citations |
| **openai_research** | OpenAI GPT | metadata | Alternative research path using OpenAI models for company intelligence |
| **stakeholder_intel** | Claude Opus + web search + thinking | .pdf | Researches the contact person: career arc, decision authority, psychological profile, tactical playbook. Branded PDF with section-aware formatting |
| **cx_intel** | Claude Sonnet + web search | .xlsx + .pdf | Scrapes reviews from Trustpilot, Google, BBB, Glassdoor, etc. Generates a 5-sheet professional Excel workbook and a branded PDF report with stat cards, theme analysis, and recommendations |
| **deck_generator** | Claude Sonnet | .pptx | Generates structured slide JSON (10 slides, 6 types), renders via python-pptx with shadcn-inspired design system, brand accent colors, rounded cards, and consistent typography |

### Phase 2 Modules

| Module | Service | Description |
|--------|---------|-------------|
| **drive_manager** | Google Drive API | Finds or creates company subfolder under BPO's root folder, uploads all artifacts via multipart upload, returns shareable links |
| **pipeline_tracker** | Google Sheets + Postgres | Updates the BPO's pipeline tracking sheet with delivery metadata (folder link, demo link, draft URL). Also upserts Postgres `pipeline_state` |
| **email_composer** | Claude Sonnet + Gmail API | Generates a professional follow-up email via Sonnet, creates it as a Gmail draft (reply-to the original sender) |
| **slack_summary** | Slack API | Posts a Block Kit delivery summary to the configured Slack channel with all Drive links and artifact details |

### Output Formatting

All generated artifacts use professional design systems:

| Artifact | Engine | Design |
|----------|--------|--------|
| **CX Intel XLSX** | openpyxl | 5-sheet workbook: Executive Summary (KPI cards), Theme Analysis (frequency bars), Consumer Reviews, Employee Reviews, Recommendations. Zinc-based palette with alternating rows |
| **CX Intel PDF** | xhtml2pdf + Jinja2 | 10-section report: stat cards, platform ratings with stars, sentiment distribution, theme detail cards, review highlights, employee sentiment, recommendations |
| **Deep Research PDF** | xhtml2pdf + Jinja2 | Branded report with cover page, table of contents, executive overview callout, key insight boxes, source citations |
| **Stakeholder Intel PDF** | xhtml2pdf + Jinja2 | Section-aware formatting: career intelligence, decision authority matrix, communication guide, tactical playbook with action items |
| **Pitch Deck PPTX** | python-pptx | shadcn-inspired design: zinc neutral base, brand accent, rounded rectangle cards, 6 slide types (title, content, stats_grid, comparison, quote, CTA), footer on every slide |

### Module Contract

Every module extends `BaseModule` and implements:

```
BaseModule (ABC)
  |
  |-- name: str                              Module identifier
  |-- should_run(ctx) -> bool                Gate: check deliverables, config
  |-- run(ctx) -> ModuleResult               Core logic (async)
  +-- execute(ctx) -> ModuleResult           Wrapper: skip check + timing + error catch
```

```
ModuleResult
  |-- module_name: str
  |-- status: "success" | "failed" | "skipped"
  |-- artifacts: list[Artifact]              Files generated (.pdf, .xlsx, etc.)
  |-- metadata: dict                         Module-specific data
  |-- duration_seconds: float
  +-- error: str | None
```

---

## API Reference

All endpoints except `/api/health`, `/slack/interactions`, and `/api/webhook/sheet-update` require `Authorization: Bearer <API_AUTH_TOKEN>`.

### Core Pipeline

```
GET  /api/health
     Response: { "status": "ok", "version": "1.0.0" }

POST /api/process
     Body: { "from_address": str, "subject": str, "body": str,
             "message_id?": str, "cc?": [str], "dry_run?": bool }
     Response: { "session_id": str, "status": "received" }

GET  /api/sessions?limit=50&status=<filter>
     Response: { "sessions": [...], "count": int }

GET  /api/sessions/{session_id}
     Response: Full session detail with context, artifacts, module results

POST /api/sessions/{session_id}/approve
     Body: { "approved_by?": str }
     Response: { "session_id": str, "status": "approved" }

POST /api/sessions/{session_id}/reject
     Body: { "reason?": str, "rejected_by?": str }
     Response: { "session_id": str, "status": "rejected" }

POST /slack/interactions
     Slack webhook for interactive button callbacks (HMAC-verified)
```

### Dashboard & Pipeline Tracking

```
GET  /api/pipeline
     Response: { "total": int, "by_status": { ... } }

GET  /api/pipeline/tracker
     Response: Combined dashboard data — partners, pipeline rows,
              timeline, stage history, stale entries (cached 60s)

GET  /api/pipeline/{bpo_key}
     Response: Pipeline rows for a specific BPO partner with
              deliverable_status booleans per company

GET  /api/timeline?bpo_key=&company=&limit=50
     Response: Deliverable completion events

GET  /api/stage-history?bpo_key=&company=&limit=50
     Response: Stage transition history

GET  /api/stale?days=7
     Response: Pipeline entries stuck in the same stage for N+ days
              (excludes companies with all deliverables complete)

POST /api/snapshot
     Response: Forces an immediate pipeline sync from Google Sheets

POST /api/webhook/sheet-update
     Receives Google Drive push notifications or secret-authenticated
     webhook calls. Triggers pipeline sync with 10s debounce.

POST /api/cleanup-duplicates
     Response: Removes duplicate deliverable_events and stage_changes
```

### Configuration & Admin

```
GET  /api/config
     Response: Redacted config (shows which keys are set, not values)

GET  /api/bpo-registry
     Response: Full BPO partner registry

GET  /api/settings/dry-run
     Response: Current DRY_RUN state

POST /api/settings/dry-run
     Body: { "enabled": bool }
     Response: Updated DRY_RUN state

GET  /api/architecture
     Response: System architecture overview (modules, endpoints, DAG)

POST /api/research
     Body: { "company": str, "research_type": str }
     Response: Triggers standalone research task
```

---

## Dashboard & Deliverable Tracking

The pipeline includes a snapshot-based deliverable tracking system that keeps a Postgres database in sync with BPO partner Google Sheets.

### How It Works

1. **Snapshot sync** — Every 5 minutes (configurable), the system reads each BPO's pipeline Google Sheet and compares to stored state in Postgres
2. **Real-time webhook** — If `PUBLIC_URL` is configured, the system registers Google Drive `files.watch` push notifications on each sheet. Changes trigger an immediate sync (with 10s debounce)
3. **Watch renewal** — Drive watches expire after 7 days. A background loop renews them every 6 hours
4. **Stale detection** — Companies stuck in the same stage for 7+ days are flagged, unless all deliverables (cx_intel, deep_dive, stakeholder_intel, presentation) are complete

### Deliverable Status

Each company row tracks 5 deliverables:

| Deliverable | Sheet Column | Status |
|-------------|-------------|--------|
| Demo | `demo_link` | Link present = complete |
| CX Intel | `consumer_intelligence_report` | Link present = complete |
| Company Deep Dive | `company_deep_dive` | Link present = complete |
| Stakeholder Intel | `stakeholder_intel` | Link present = complete |
| Presentation | `presentation` | Link present = complete |

### Database Schema

```sql
-- Pipeline state (synced from Google Sheets)
CREATE TABLE pipeline_state (
    id SERIAL PRIMARY KEY,
    bpo_key TEXT NOT NULL,
    company TEXT NOT NULL,
    stage TEXT DEFAULT '',
    demo_link TEXT DEFAULT '',
    cx_intel_link TEXT DEFAULT '',
    deep_dive_link TEXT DEFAULT '',
    stakeholder_link TEXT DEFAULT '',
    presentation_link TEXT DEFAULT '',
    -- ... additional sheet columns ...
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(bpo_key, company)
);

-- Deliverable completion events
CREATE TABLE deliverable_events (
    id SERIAL PRIMARY KEY,
    bpo_key TEXT NOT NULL,
    company TEXT NOT NULL,
    deliverable_type TEXT NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    link_value TEXT DEFAULT ''
);

-- Stage transition history
CREATE TABLE stage_changes (
    id SERIAL PRIMARY KEY,
    bpo_key TEXT NOT NULL,
    company TEXT NOT NULL,
    old_stage TEXT DEFAULT '',
    new_stage TEXT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Session persistence
CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'received',
    raw_email JSONB NOT NULL,
    context JSONB NOT NULL DEFAULT '{}',
    bpo_key TEXT,
    target_company TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved_by TEXT,
    rejected_by TEXT,
    reject_reason TEXT
);
```

---

## Session Lifecycle

```
                    +----------+
                    | received |  <-- POST /api/process or Gmail poller
                    +----+-----+
                         |
                         v
                   +-----+-------+
                   | classifying |  <-- Phase 1 running (8 modules)
                   +-----+-------+
                         |
                +--------+--------+
                |                 |
                v                 v
  +-------------+---+     +------+------+
  | awaiting_       |     |    error    |  <-- Phase 1 failed
  | approval        |     +------+------+
  +---+--------+----+            |
      |        |                 | (can retry via approve)
      |        |                 |
      v        v                 |
 +----+---+ +--+------+         |
 |approved| |rejected |         |
 +----+---+ +---------+         |
      |                         |
      v                         |
 +----+------+                  |
 | delivering|  <-- Phase 2 running (4 modules)
 +----+------+                  |
      |                         |
      +-------+---------+      |
              |         |      |
              v         v      v
         +----+---+ +---+----+
         |complete| | error  |
         +--------+ +--------+
```

---

## External Services

```
+-------------------+     +--------------------+     +-------------------+
|   Anthropic API   |     |   Google Cloud     |     |     Slack API     |
|                   |     |                    |     |                   |
| Claude Sonnet 4.6 |     | Gmail API          |     | chat.postMessage  |
|  - classify       |     |  - poll unread     |     | chat.update       |
|  - deck slides    |     |  - mark as read    |     | Interactive msgs  |
|  - email draft    |     |  - create draft    |     | HMAC verification |
|  - cx scraping    |     |                    |     |                   |
|                   |     | Drive API          |     +-------------------+
| Claude Opus 4.6   |     |  - create folders  |
|  - deep research  |     |  - upload files    |     +-------------------+
|  - stakeholder    |     |  - share links     |     |    Brand.dev      |
|  (extended think  |     |  - files.watch     |     |                   |
|   + web search)   |     |  (push notify)     |     | /v1/brand         |
|                   |     |                    |     |  - colors         |
+-------------------+     | Sheets API         |     |  - logos          |
                          |  - read/append     |     |  - fonts          |
+-------------------+     |  - update rows     |     +-------------------+
|   OpenAI API      |     +--------------------+
|                   |
| GPT research      |     +-------------------+
| (alt. module)     |     |   PostgreSQL 16   |
+-------------------+     |                   |
                          | sessions          |
                          | pipeline_state    |
                          | deliverable_events|
                          | stage_changes     |
                          +-------------------+
```

### Dependency Matrix

```
                          REQUIRED    OPTIONAL
                          --------    --------
Anthropic API               [X]
Google OAuth (Gmail)         [X]
Google Drive                 [X]
Google Sheets                           [X]   (per-BPO, some have no sheet)
Brand.dev                               [X]   (falls back to default palette)
OpenAI API                              [X]   (alt. research module)
Slack                                   [X]   (skipped if no token)
PostgreSQL                              [X]   (sessions ephemeral without it)
```

---

## Setup & Installation

### Prerequisites

- Python 3.12+
- Docker & Docker Compose (for containerized deployment)
- PostgreSQL 16 (optional, included in docker-compose)

### Install

```bash
git clone https://github.com/anyreachai/bpo-sales-ops-agent.git
cd bpo-sales-ops-agent
pip install -e ".[dev]"
```

### Configure

```bash
cp .env.example .env
# Edit .env with your credentials
```

---

## Configuration

All configuration is via environment variables (loaded from `.env`):

### Required

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Claude API key (Sonnet + Opus) |
| `GOOGLE_OAUTH_CLIENT_ID` | Google OAuth client ID |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Google OAuth client secret |
| `GOOGLE_OAUTH_REFRESH_TOKEN` | Gmail/Drive/Sheets token |
| `SLACK_BOT_TOKEN` | Slack bot token (`xoxb-...`) |
| `SLACK_SIGNING_SECRET` | Slack webhook HMAC secret |

### Required for Specific Modules

| Variable | Description |
|----------|-------------|
| `OPENAI_API_KEY` | OpenAI research module |
| `BRAND_DEV_API_KEY` | Brand.dev logo/color lookups (falls back to defaults) |

### Webhook & Deployment

| Variable | Default | Description |
|----------|---------|-------------|
| `PUBLIC_URL` | `""` | Railway public URL — enables Drive push notifications for real-time sheet sync |
| `SHEET_WEBHOOK_SECRET` | `sheet-sync-2026` | Secret for webhook authentication (set to a strong random value in production) |
| `API_AUTH_TOKEN` | `bpo-ops-dash-2026` | Bearer token for API authentication |
| `DATABASE_URL` | `""` | PostgreSQL connection string |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `SLACK_NOTIFY_CHANNEL` | `C0AQN1FNXNE` | Slack channel ID for notifications |
| `CORS_ORIGINS` | `*` | Allowed CORS origins (lock down in production) |
| `POLL_INTERVAL_SECONDS` | `300` | Gmail + sheet sync fallback polling interval (seconds) |
| `APPROVAL_TIMEOUT_HOURS` | `4` | Hours before unapproved deliverables auto-expire |
| `DRY_RUN` | `false` | Skip Drive/Gmail/Slack calls |
| `TEMP_DIR` | `/tmp/bpo-ops` | Artifact staging directory |
| `BPO_DOMAINS` | `resultscx.com,...` | Comma-separated BPO domains for Gmail filter |

### Google OAuth Scopes

```
https://www.googleapis.com/auth/gmail.modify
https://www.googleapis.com/auth/gmail.compose
https://www.googleapis.com/auth/drive.file
https://www.googleapis.com/auth/spreadsheets
```

### Slack Bot Scopes

```
chat:write
chat:write.public
```

---

## Deployment

### Railway (Production)

The project deploys to Railway with auto-deploy from the `main` branch.

**Files:**
- `railway.toml` — Points to `infra/Dockerfile`, healthcheck at `/api/health`, ON_FAILURE restart policy
- `infra/Dockerfile` — Python 3.12-slim with system deps for reportlab, pycairo, and psycopg2
- `.dockerignore` — Excludes `.env`, tests, `.git`, markdown files

**Setup:**
1. Create a Railway project with a Postgres service
2. Add a new service from the GitHub repo
3. Set all environment variables (see Configuration above)
4. `DATABASE_URL` should reference the Postgres service: `${{Postgres.DATABASE_URL}}`
5. After deploy, set `PUBLIC_URL` to the generated Railway domain

**Domain:** `https://bpo-sales-ops-agent-production.up.railway.app`

### Docker Compose (Local)

```bash
docker compose -f infra/docker-compose.yml up --build
```

Starts the FastAPI app on port 8080 and Postgres on 5432.

---

## Running

### Local Python

```bash
uvicorn orchestrator.main:app --host 0.0.0.0 --port 8080 --reload
```

### What Happens on Startup

```
1. ensure_schema()              Create sessions table (or log warning if no DB)
2. ensure_tracker_schema()      Create pipeline_state, deliverable_events, stage_changes tables
3. register_all()               Register all 12 pipeline modules
4. _gmail_poll_loop()           Start background Gmail poller (if credentials set)
5. _pipeline_sync_loop()        Start background sheet sync (every POLL_INTERVAL_SECONDS)
6. _register_sheet_watches()    Register Drive push notifications (if PUBLIC_URL set)
7. _sheet_watch_renewal_loop()  Renew Drive watches every 6 hours
```

### Quick Smoke Test

```bash
# Health check
curl http://localhost:8080/api/health

# Submit a test email (dry run)
curl -X POST http://localhost:8080/api/process \
  -H "Authorization: Bearer bpo-ops-dash-2026" \
  -H "Content-Type: application/json" \
  -d '{
    "from_address": "jarmstrong@resultscx.com",
    "subject": "GameStop full package",
    "body": "Prepare everything for GameStop. Contact: Jane Smith, VP CX. gamestop.com",
    "dry_run": true
  }'

# Check pipeline dashboard
curl -H "Authorization: Bearer bpo-ops-dash-2026" \
  http://localhost:8080/api/pipeline/tracker

# Force a sheet sync
curl -X POST -H "Authorization: Bearer bpo-ops-dash-2026" \
  http://localhost:8080/api/snapshot
```

---

## Testing

### Run the Full Suite

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

```
116 passed in ~3 minutes
Zero network calls — all external services are mocked
```

### Test Coverage

| File | Tests | Covers |
|------|-------|--------|
| `test_api.py` | 15+ | Health, auth, process, sessions CRUD, reject, Slack buttons, webhook, tracker, dashboard endpoints |
| `test_classifier.py` | 22 | 5 BPO domains + unknown, extraction, code fence stripping, run success/fail, "everything" expansion, 10 fixtures |
| `test_dag.py` | 12+ | Node structure, execution order, error isolation, context accumulation, skipping |
| `test_e2e.py` | 6+ | Full Phase 1+2, orchestrator flow, partial deliverables, state transitions, rejection flow |
| `test_gmail_poller.py` | 14 | Body extraction (5 variants), skip filters, dedup, prune, poll_once |
| `test_modules.py` | 30+ | All 12 modules: should_run, run success, edge cases |

### Mocking Strategy

All external services are intercepted at the `shared/` utility layer:

| External Service | Mock Target | Behavior |
|-----------------|-------------|----------|
| Anthropic Sonnet | `shared.anthropic_client.call_sonnet` | Route by system prompt keywords |
| Anthropic Opus | `shared.anthropic_client.call_opus_with_search` | Route by prompt keywords |
| Google OAuth | `shared.google_auth.get_access_token` | Return `"mock_token"` |
| HTTP (Drive, Gmail, Sheets, Slack, Brand.dev) | `httpx.AsyncClient` | MockAsyncClient routes by URL prefix |
| PostgreSQL | `settings.DATABASE_URL = ""` | Session funcs early-return |
| File system | `shared.storage.TEMP_DIR` | pytest tmp_path |

---

## BPO Partner Registry

Defined in `config/bpo_registry.json`:

| Key | Name | Email Domains | Drive Folder | Sheet |
|-----|------|--------------|-------------|-------|
| test | Test BPO | test.com, anyreach.ai | (test) | Yes |
| resultscx | ResultsCX | resultscx.com | 1P9kkcNd... | Yes |
| esal | eSAL | esal.com, esalglobal.com | 1-uIENKNc... | Yes |
| startek | Startek | startek.com | 1mg74nTK... | Yes |
| cgs | CGS | cgsinc.com | 1gYImwsn0... | No |
| cp360 | CP360 | cp360.com | 1vDVJh9ew... | Yes |

Each partner has:
- **Email domains** — used by classifier to match inbound sender
- **Drive folder ID** — root folder where artifacts are uploaded
- **Pipeline sheet ID** — Google Sheet for tracking (nullable)
- **Key contacts** — known sender names for the BPO

---

## License

Proprietary — Anyreach, Inc.
