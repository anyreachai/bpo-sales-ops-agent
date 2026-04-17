# BPO Sales Ops Pipeline

An AI-powered sales operations pipeline that automatically processes inbound BPO partner emails, generates comprehensive sales artifacts, and delivers them through a human-in-the-loop approval workflow.

```
  +-----------+      +----------------+      +------------------+      +-----------+
  |   Gmail   | ---> |  FastAPI + DAG | ---> |  Slack Approval  | ---> |  Google   |
  |   Inbox   |      |  (11 Modules)  |      |  (Approve/Reject)|      |  Drive    |
  +-----------+      +----------------+      +------------------+      +-----------+
       |                    |                        |                       |
   BPO emails          Claude AI              Human review             Artifacts
   auto-polled       generates docs          via Slack buttons       auto-delivered
```

---

## Table of Contents

- [How It Works](#how-it-works)
- [Architecture](#architecture)
- [Pipeline DAG](#pipeline-dag)
- [Module Reference](#module-reference)
- [API Reference](#api-reference)
- [Session Lifecycle](#session-lifecycle)
- [Project Structure](#project-structure)
- [External Services](#external-services)
- [Setup & Installation](#setup--installation)
- [Configuration](#configuration)
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

```
 INBOUND EMAIL                         PHASE 1                              GATE                    PHASE 2
 from BPO partner                  (Classification &                   (Human Review)            (Delivery)
                                    Content Generation)

 +------------------+     +-------------+     +------------------+     +----------+     +------------------+
 | jordan@resultscx |     |             |     | brand_extractor  |     |          |     | drive_manager    |
 | .com             |---->| classifier  |---->| demo_generator   |     |  Slack   |     |   (upload all)   |
 |                  |     |             |     | deep_research    |---->| Approve/ |---->| pipeline_tracker |
 | "Prepare package |     | - match BPO |     | stakeholder_intel|     |  Reject  |     | email_composer   |
 |  for GameStop"   |     | - extract   |     | cx_intel         |     |  Buttons |     | slack_summary    |
 |                  |     |   company   |     +--------+---------+     |          |     +------------------+
 +------------------+     | - parse     |              |               +----------+
                          |   intent    |     +--------v---------+
                          +-------------+     | deck_generator   |
                                              | (needs all above)|
                                              +------------------+
```

---

## Architecture

```
bpo-sales-ops/
 |
 |-- orchestrator/          Core engine
 |   |-- main.py            FastAPI app, endpoints, lifespan, Slack handler
 |   |-- dag.py             DAG runner with dependency resolution
 |   |-- session.py         PostgreSQL session persistence
 |   |-- config.py          Pydantic settings (env vars)
 |   +-- registry.py        Module registration system
 |
 |-- modules/               11 pipeline modules (each has module.py)
 |   |-- classifier/        Email classification + BPO matching
 |   |-- brand_extractor/   Brand.dev API + color palette generation
 |   |-- demo_generator/    Demo link lookup
 |   |-- deep_research/     Claude Opus + web search -> .docx report
 |   |-- stakeholder_intel/ Claude Opus + web search -> .pdf brief
 |   |-- cx_intel/          Review scraping -> .xlsx + .pdf reports
 |   |-- deck_generator/    AI slide content -> .pptx pitch deck
 |   |-- drive_manager/     Google Drive upload + folder management
 |   |-- pipeline_tracker/  Google Sheets + Postgres pipeline state
 |   |-- email_composer/    AI email draft -> Gmail draft creation
 |   +-- slack_manager/     Block Kit summary message
 |
 |-- shared/                Cross-cutting utilities
 |   |-- anthropic_client.py  Claude Sonnet + Opus wrappers
 |   |-- google_auth.py       OAuth token refresh
 |   |-- storage.py           Artifact file paths
 |   +-- types.py             Pydantic models (SessionContext, Artifact, etc.)
 |
 |-- gmail_poller/          Background email ingestion
 |   +-- poller.py           Gmail API polling with dedup + skip filters
 |
 |-- config/
 |   +-- bpo_registry.json   5 BPO partner definitions
 |
 |-- infra/
 |   |-- Dockerfile          Python 3.12-slim, port 8080
 |   +-- docker-compose.yml  App + Postgres 16
 |
 +-- tests/                 103 tests, all passing
     |-- conftest.py         Shared fixtures + MockAsyncClient
     |-- mocks.py            Mock API responses
     |-- test_api.py         11 endpoint tests
     |-- test_classifier.py  22 classification tests
     |-- test_dag.py         12 DAG execution tests
     |-- test_e2e.py          6 full pipeline tests
     |-- test_gmail_poller.py 14 poller tests
     +-- test_modules.py     28 module unit tests
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
            +-----------+-----------+--+--+-----------+-----------+
            |           |           |     |           |           |
            v           v           v     v           v           |
  +---------+--+ +------+-----+ +--+--+ +-+--------+ +-+-------+ |  Batch 1
  |   brand    | |    deep    | |demo | |stakeholder| |cx_intel | |  (parallel)
  |  extractor | |  research  | |gen  | |  intel    | |         | |
  |            | |            | |     | |           | |         | |
  | Brand.dev  | | Opus +     | | DB  | | Opus +    | | Sonnet +| |
  | API + HSL  | | web search | | lkp | | web srch  | | scraper | |
  | palette    | | -> .docx   | |     | | -> .pdf   | | -> .xlsx| |
  +-----+------+ +-----+------+ +--+--+ +-----+-----+ +---+----+ |
        |               |          |           |            |      |
        +-------+-------+----------+-----------+------------+      |
                |                                                  |
                v                                                  |
       +--------+---------+                                        |
       |  deck_generator  |  Batch 2 (waits for all above)        |
       |                  |                                        |
       | Sonnet generates |                                        |
       | slide JSON ->    |                                        |
       | python-pptx      |                                        |
       | -> .pptx         |                                        |
       +------------------+                                        |

  Status: received -> classifying -> awaiting_approval
  Artifacts: .docx  .pdf  .xlsx  .pptx  .json (brand guide)
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
  |  * gamestop_Deep_Research.docx (deep_research)                 |
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
| **deep_research** | Claude Opus + web search + thinking | .docx | Extended thinking (10k budget) + web search produces a multi-section markdown report, converted to styled Word doc with cover page and tables |
| **stakeholder_intel** | Claude Opus + web search + thinking | .pdf | Researches the contact person: career arc, budget control, LinkedIn intel, psychological profile, tactical playbook. ReportLab PDF output |
| **cx_intel** | Claude Sonnet + web search | .xlsx + .pdf | Scrapes reviews from Trustpilot, Google, BBB, Glassdoor, etc. Generates an Excel workbook (4 sheets) and a branded PDF report |
| **deck_generator** | Claude Sonnet | .pptx | Generates structured slide JSON (10 slides), renders via python-pptx with brand palette, 16:9 widescreen |

### Phase 2 Modules

| Module | Service | Description |
|--------|---------|-------------|
| **drive_manager** | Google Drive API | Finds or creates company subfolder under BPO's root folder, uploads all artifacts via multipart upload, returns shareable links |
| **pipeline_tracker** | Google Sheets + Postgres | Updates the BPO's pipeline tracking sheet with delivery metadata (folder link, demo link, draft URL). Also upserts Postgres `pipeline_state` |
| **email_composer** | Claude Sonnet + Gmail API | Generates a professional follow-up email via Sonnet, creates it as a Gmail draft (reply-to the original sender) |
| **slack_summary** | Slack API | Posts a Block Kit delivery summary to the configured Slack channel with all Drive links and artifact details |

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
  |-- artifacts: list[Artifact]              Files generated (.docx, .pdf, etc.)
  |-- metadata: dict                         Module-specific data
  |-- duration_seconds: float
  +-- error: str | None
```

---

## API Reference

All endpoints except `/api/health` and `/slack/interactions` require `Authorization: Bearer <API_AUTH_TOKEN>`.

### Endpoints

```
GET  /api/health
     Response: { "status": "ok", "version": "1.0.0" }

POST /api/process
     Body: { "from_address": str, "subject": str, "body": str,
             "message_id?": str, "cc?": [str], "dry_run?": bool }
     Response: { "session_id": str, "status": "received" }
     Effect: Creates session, kicks off Phase 1 in background

GET  /api/sessions?limit=50&status=<filter>
     Response: { "sessions": [...], "count": int }

GET  /api/sessions/{session_id}
     Response: Full session detail with context, artifacts, module results

POST /api/sessions/{session_id}/approve
     Body: { "approved_by?": str }
     Response: { "session_id": str, "status": "approved" }
     Effect: Kicks off Phase 2 in background
     Error: 409 if status is not "awaiting_approval" or "error"

POST /api/sessions/{session_id}/reject
     Body: { "reason?": str, "rejected_by?": str }
     Response: { "session_id": str, "status": "rejected" }

GET  /api/pipeline
     Response: Aggregate counts by status

GET  /api/config
     Response: Redacted config (shows which keys are set, not values)

POST /slack/interactions
     Slack webhook for interactive button callbacks (HMAC-verified)
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
                   | classifying |  <-- Phase 1 running (7 modules)
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

### Database Schema

```sql
CREATE TABLE sessions (
    session_id       TEXT PRIMARY KEY,
    status           TEXT NOT NULL DEFAULT 'received',
    raw_email        JSONB NOT NULL,
    context          JSONB NOT NULL DEFAULT '{}',
    bpo_key          TEXT,
    target_company   TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved_by      TEXT,
    rejected_by      TEXT,
    reject_reason    TEXT
);

CREATE TABLE pipeline_state (
    id               SERIAL PRIMARY KEY,
    bpo_key          TEXT NOT NULL,
    company_name     TEXT NOT NULL,
    stage            TEXT,
    drive_folder_url TEXT,
    demo_link        TEXT,
    draft_url        TEXT,
    dry_run          BOOLEAN DEFAULT FALSE,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(bpo_key, company_name)
);
```

---

## Project Structure

```
bpo-sales-ops/
|
|-- orchestrator/
|   |-- __init__.py
|   |-- main.py              557 lines   FastAPI app, 9 endpoints, Slack handler
|   |-- dag.py                68 lines   DAG runner, NODES dict, Phase enum
|   |-- session.py           141 lines   Postgres CRUD for sessions
|   |-- config.py             32 lines   Pydantic settings, env vars
|   +-- registry.py           22 lines   Module registration
|
|-- modules/
|   |-- __init__.py                      register_all() for 11 modules
|   |-- _base.py              40 lines   BaseModule ABC
|   |-- classifier/
|   |   |-- module.py        163 lines   BPO matching + Sonnet classification
|   |   +-- prompts.py                   CLASSIFY_SYSTEM prompt
|   |-- brand_extractor/
|   |   |-- module.py        258 lines   Brand.dev + HSL palette builder
|   |   +-- client.py         54 lines   Brand.dev API client
|   |-- deep_research/
|   |   |-- module.py        522 lines   Opus research + markdown-to-docx
|   |   +-- prompts.py                   Research prompt template
|   |-- stakeholder_intel/
|   |   |-- module.py        674 lines   Opus stakeholder + ReportLab PDF
|   |   +-- prompts.py                   Stakeholder prompt template
|   |-- cx_intel/
|   |   |-- module.py                    Orchestrates scraper + generators
|   |   |-- scraper.py       212 lines   Sonnet + web search review scraper
|   |   |-- xlsx_generator.py            4-sheet Excel workbook
|   |   +-- pdf_generator.py             Branded CX intel PDF
|   |-- deck_generator/
|   |   |-- module.py                    Sonnet slide JSON + renderer
|   |   +-- templates.py     843 lines   python-pptx slide builders
|   |-- demo_generator/
|   |   +-- module.py         89 lines   DB lookup or action_required
|   |-- drive_manager/
|   |   +-- module.py        197 lines   Drive folder + multipart upload
|   |-- pipeline_tracker/
|   |   +-- module.py        316 lines   Sheets + Postgres upsert
|   |-- email_composer/
|   |   +-- module.py        248 lines   Sonnet email + Gmail draft
|   +-- slack_manager/
|       |-- module.py        159 lines   Slack postMessage
|       +-- blocks.py                    Block Kit message builder
|
|-- shared/
|   |-- __init__.py
|   |-- anthropic_client.py   44 lines   call_sonnet(), call_opus_with_search()
|   |-- google_auth.py        31 lines   OAuth token refresh with caching
|   |-- storage.py            18 lines   artifact_path(), ensure_session_dir()
|   |-- types.py              87 lines   EmailPayload, BPOPartner, SessionContext, etc.
|   +-- errors.py             16 lines   Custom exceptions
|
|-- gmail_poller/
|   |-- __init__.py
|   +-- poller.py            153 lines   Gmail API polling, dedup, skip filters
|
|-- config/
|   +-- bpo_registry.json     42 lines   5 BPO partner definitions
|
|-- infra/
|   |-- Dockerfile            18 lines   Python 3.12-slim
|   +-- docker-compose.yml    24 lines   App + Postgres 16
|
|-- tests/
|   |-- fixtures/                        10 test email JSON files
|   |-- conftest.py          414 lines   Fixtures, MockAsyncClient
|   |-- mocks.py             285 lines   Mock API responses
|   |-- test_api.py          168 lines   11 endpoint tests
|   |-- test_classifier.py   162 lines   22 classification tests
|   |-- test_dag.py          162 lines   12 DAG execution tests
|   |-- test_e2e.py          202 lines    6 full pipeline tests
|   |-- test_gmail_poller.py 176 lines   14 poller tests
|   +-- test_modules.py      318 lines   28 module unit tests
|
|-- pyproject.toml                       Dependencies + pytest config
|-- .env.example                         All env vars documented
+-- .gitignore
```

**Total: 56 Python files, 7,867 lines of code, 103 tests**

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
|  (extended think  |     |                    |     |                   |
|   + web search)   |     | Sheets API         |     | /v1/brand         |
|                   |     |  - read/append     |     |  - colors         |
+-------------------+     |  - update rows     |     |  - logos          |
                          +--------------------+     |  - fonts          |
+-------------------+                                +-------------------+
|   PostgreSQL 16   |
|                   |
| sessions table    |
| pipeline_state    |
| (optional - runs  |
|  without DB too)  |
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

```
+-------------------------------+----------+---------------------------------------------+
| Variable                      | Required | Description                                 |
+-------------------------------+----------+---------------------------------------------+
| ANTHROPIC_API_KEY             |   YES    | Claude API key                              |
| GOOGLE_OAUTH_CLIENT_ID        |   YES    | Google OAuth client ID                      |
| GOOGLE_OAUTH_CLIENT_SECRET    |   YES    | Google OAuth client secret                  |
| GOOGLE_OAUTH_REFRESH_TOKEN    |   YES*   | Gmail/Drive/Sheets token (* poller needs it)|
| BRAND_DEV_API_KEY             |   no     | Brand.dev API key (fallback if missing)     |
| SLACK_BOT_TOKEN               |   no     | Slack bot token (xoxb-...)                  |
| SLACK_SIGNING_SECRET          |   no     | Slack webhook HMAC secret                   |
| SLACK_NOTIFY_CHANNEL          |   no     | Slack channel ID (default: C0AQN1FNXNE)     |
| DATABASE_URL                  |   no     | PostgreSQL connection string                |
| API_AUTH_TOKEN                |   no     | Bearer token (default: bpo-ops-dash-2026)   |
| DRY_RUN                       |   no     | Skip Drive/Gmail/Slack calls (default: false|
| POLL_INTERVAL_SECONDS         |   no     | Gmail poll frequency (default: 300)         |
| CORS_ORIGINS                  |   no     | Allowed origins (default: *)                |
| TEMP_DIR                      |   no     | Artifact directory (default: /tmp/bpo-ops)  |
| BPO_DOMAINS                   |   no     | Comma-sep BPO domains for Gmail filter      |
+-------------------------------+----------+---------------------------------------------+
```

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

## Running

### Docker Compose (recommended)

```bash
docker compose -f infra/docker-compose.yml up --build
```

Starts the FastAPI app on port 8080 and Postgres on 5432.

### Local Python

```bash
uvicorn orchestrator.main:app --host 0.0.0.0 --port 8080 --reload
```

### What Happens on Startup

```
1. ensure_schema()        Create Postgres tables (or log warning if no DB)
2. register_all()         Register all 11 pipeline modules
3. _gmail_poll_loop()     Start background poller (if GOOGLE_OAUTH_REFRESH_TOKEN set)
                          Polls every POLL_INTERVAL_SECONDS (default 300s)
```

### Quick Smoke Test

```bash
# Health check
curl http://localhost:8080/api/health

# Submit a test email
curl -X POST http://localhost:8080/api/process \
  -H "Authorization: Bearer bpo-ops-dash-2026" \
  -H "Content-Type: application/json" \
  -d '{
    "from_address": "jarmstrong@resultscx.com",
    "subject": "GameStop full package",
    "body": "Prepare everything for GameStop. Contact: Jane Smith, VP CX. gamestop.com",
    "dry_run": true
  }'

# Poll session status
curl -H "Authorization: Bearer bpo-ops-dash-2026" \
  http://localhost:8080/api/sessions
```

---

## Testing

### Run the Full Suite

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

```
103 passed in ~3 seconds
Zero network calls — all external services are mocked
```

### Test Coverage

```
+----------------------+-------+--------------------------------------------+
| File                 | Tests | Covers                                     |
+----------------------+-------+--------------------------------------------+
| test_api.py          |    11 | Health, auth (401/403/200), process,       |
|                      |       | sessions CRUD, reject, Slack buttons       |
+----------------------+-------+--------------------------------------------+
| test_classifier.py   |    22 | 5 BPO domains + unknown, extraction,       |
|                      |       | code fence stripping, run success/fail,    |
|                      |       | "everything" expansion, 10 fixtures        |
+----------------------+-------+--------------------------------------------+
| test_dag.py          |    12 | Node structure, execution order, error     |
|                      |       | isolation, context accumulation, skipping   |
+----------------------+-------+--------------------------------------------+
| test_e2e.py          |     6 | Full Phase 1+2, orchestrator flow,         |
|                      |       | partial deliverables, state transitions,   |
|                      |       | rejection flow                             |
+----------------------+-------+--------------------------------------------+
| test_gmail_poller.py |    14 | Body extraction (5 variants), skip         |
|                      |       | filters, dedup, prune, poll_once (4)       |
+----------------------+-------+--------------------------------------------+
| test_modules.py      |    28 | All 10 non-classifier modules:             |
|                      |       | should_run, run success, edge cases        |
+----------------------+-------+--------------------------------------------+
```

### Mocking Strategy

All external services are intercepted at the `shared/` utility layer:

```
+---------------------+----------------------------------+----------------------+
| External Service    | Mock Target                      | Behavior             |
+---------------------+----------------------------------+----------------------+
| Anthropic Sonnet    | shared.anthropic_client          | Route by system      |
|                     |   .call_sonnet                   | prompt keywords      |
+---------------------+----------------------------------+----------------------+
| Anthropic Opus      | shared.anthropic_client          | Route by prompt      |
|                     |   .call_opus_with_search         | keywords             |
+---------------------+----------------------------------+----------------------+
| Google OAuth        | shared.google_auth               | Return "mock_token"  |
|                     |   .get_access_token              |                      |
+---------------------+----------------------------------+----------------------+
| HTTP (Drive, Gmail, | httpx.AsyncClient                | MockAsyncClient      |
|  Sheets, Slack,     |                                  | routes by URL prefix |
|  Brand.dev)         |                                  |                      |
+---------------------+----------------------------------+----------------------+
| PostgreSQL          | settings.DATABASE_URL = ""       | Session funcs        |
|                     |                                  | early-return         |
+---------------------+----------------------------------+----------------------+
| File system         | shared.storage.TEMP_DIR          | pytest tmp_path      |
+---------------------+----------------------------------+----------------------+
```

### Test Fixtures

10 email fixtures in `tests/fixtures/` covering all BPO partners and edge cases:

```
cgs_no_sheet.json          CGS partner (no pipeline sheet configured)
cp360_full_package.json    CP360, all deliverables
demo_only.json             Single deliverable request
esal_fabletics.json        eSAL partner, Fabletics prospect
missing_intake.json        Incomplete intake information
resultscx_followup.json    Follow-up email (not new request)
resultscx_gamestop.json    Standard full request
startek_ambiguous.json     Ambiguous intent
startek_cx_intel_only.json Partial deliverable (CX only)
unknown_domain.json        Non-BPO sender domain
```

---

## BPO Partner Registry

Defined in `config/bpo_registry.json`:

```
+----------+----------+---------------------------+----------------+-----------+
| Key      | Name     | Email Domains             | Drive Folder   | Sheet     |
+----------+----------+---------------------------+----------------+-----------+
| resultscx| ResultsCX| resultscx.com             | 1P9kkcNd...    | 1LYhYMYX..|
| esal     | eSAL     | esal.com, esalglobal.com  | 1-uIENKNc...   | 1M_fnzR1..|
| startek  | Startek  | startek.com               | 1mg74nTK...    | 1lj7F2F2..|
| cgs      | CGS      | cgsinc.com                | 1gYImwsn0...   | (none)    |
| cp360    | CP360    | cp360.com                 | 1vDVJh9ew...   | 1dnWo4JY..|
+----------+----------+---------------------------+----------------+-----------+
```

Each partner has:
- **Email domains** — used by classifier to match inbound sender
- **Drive folder ID** — root folder where artifacts are uploaded
- **Pipeline sheet ID** — Google Sheet for tracking (nullable)
- **Key contacts** — known sender names for the BPO

---

## License

Proprietary — Anyreach, Inc.
