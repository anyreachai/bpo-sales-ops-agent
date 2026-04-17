# BPO Sales Ops Dashboard — Product Requirements

## Overview

Internal dashboard for Anyreach's BPO Sales Ops Agent. The agent automates prospect enablement for BPO (Business Process Outsourcing) partners — when a BPO partner emails requesting demos, research reports, or pitch materials for their end client, the agent classifies the request, generates research reports, and orchestrates file delivery across Google Drive, Sheets, Slack, and Gmail.

This dashboard replaces terminal/CLI interactions. It's used by an internal team of 3-5 people.

## Tech Stack

- **Frontend**: React + TypeScript + Tailwind CSS + shadcn/ui
- **Backend API**: Already built and deployed (see API section below)
- **Auth**: Bearer token passed in `Authorization` header on all API calls

## Backend API

**Base URL**: `http://localhost:8080` (local) or your deployed URL
**Auth**: All `/api/*` endpoints require `Authorization: Bearer <API_AUTH_TOKEN>`

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | System status (no auth) |
| `POST` | `/api/process` | Submit email for processing (triggers Phase 1) |
| `GET` | `/api/sessions` | List sessions (`?limit=50&status=awaiting_approval`) |
| `GET` | `/api/sessions/{session_id}` | Single session detail with artifacts |
| `POST` | `/api/sessions/{session_id}/approve` | Approve → triggers Phase 2 |
| `POST` | `/api/sessions/{session_id}/reject` | Reject session |
| `GET` | `/api/pipeline` | Pipeline summary stats |
| `GET` | `/api/config` | Current config (secrets redacted) |
| `POST` | `/slack/interactions` | Slack button callbacks (HMAC auth) |

See `README.md` for full endpoint schemas with request/response examples.

---

## Pages

### 1. Dashboard (Home)

The main overview page. Shows at-a-glance system status and pending work.

**Layout:**
- **Status bar** at top: system health indicator (green/red dot), pending approvals count, total sessions processed
- **Pending Approvals section**: Cards for each pending approval (see Approval Card below). If no pending approvals, show empty state "No pending approvals"
- **Recent Activity**: Table of last 10 sessions showing status, BPO, company, time ago

**Data sources:**
- `GET /api/health` — poll every 30 seconds for live status
- `GET /api/sessions?status=awaiting_approval` — pending approvals list
- `GET /api/sessions?limit=10` — recent sessions

---

### 2. Approvals

Full list of pending approvals with detailed cards and action buttons.

**Approval Card layout:**
Each pending approval should be displayed as an expandable card with:

- **Header row**: Company name (large), BPO partner badge, time since created
- **Info grid** (2 columns):
  - BPO Partner (formatted name, e.g., "ResultsCX")
  - Requested By: name + email
  - Company + website link
  - Total Deliverables count
- **Deliverables Required**: Bullet list with friendly labels:
  - `demo` → "Demo"
  - `deep_research` → "Deep Research Report (PDF)"
  - `stakeholder_intel` → "Stakeholder Intel Brief (PDF)"
  - `cx_intel` → "CX Intel Report (PDF)"
  - `pitch_deck` → "Personalized Pitch Deck"
- **Generated in Phase 1**: List of PDFs generated (file names)
- **Pending Actions**: List of actions that will execute on approval. Each action should show:
  - Friendly name (e.g., "Upload to Drive" not "upload_to_drive")
  - Links where applicable (Drive folder link, Google Sheets link)
- **Request Details** (collapsible): The original email body
- **Action buttons**: Green "Approve" button, Red "Reject" button with confirmation dialog
- **Expiry indicator**: "Expires in X hours" countdown based on `expires_at`

**Actions:**
- Approve → `POST /api/sessions/{session_id}/approve` → show success toast, remove card
- Reject → confirmation dialog with optional reason → `POST /api/sessions/{session_id}/reject` → show toast, remove card

**Data source:** `GET /api/sessions?status=awaiting_approval`

---

### 3. Sessions (History)

Searchable, filterable table of all processed sessions.

**Table columns:**
- Status (color-coded badge)
- Company
- BPO Partner
- Requested By
- Subject
- Phase 1 Duration
- Phase 2 Duration
- Created At (relative time, e.g., "2 hours ago")

**Status badges with colors:**
- `processing` → yellow/amber
- `phase1_complete` → blue
- `no_plan` → gray
- `approved` → green outline
- `phase2_complete` → green solid
- `rejected` → red
- `failed` → red with error icon
- `expired` → gray with clock icon

**Filters:**
- Status dropdown (multi-select)
- BPO partner dropdown
- Date range

**Row click** → expands to show full session detail:
- All fields from `GET /api/sessions/{session_id}`
- Deliverables generated
- Errors (if any)
- Approved/rejected by whom

**Data source:** `GET /api/sessions?limit=50&status=...`

---

### 4. Submit Request

Form to manually submit a BPO email for processing (replaces `curl -X POST /process`).

**Form fields:**
- **From** (email input, required): Sender email address
- **Subject** (text input, required): Email subject line
- **Gmail Message ID** (text input, optional): For "Open in Gmail" link
- **Body** (textarea, required): Full email body

**BPO quick-fill**: When the user types an email in "From", auto-detect if the domain matches a known BPO partner and show a badge (e.g., typing `@startek.com` shows "Startek" badge).

**Submit behavior:**
- Show loading state with progress message ("Phase 1 running... this takes 5-8 minutes")
- Call `POST /api/process`
- On completion, show result summary and link to the new approval (if execution plan was generated)
- If no execution plan (email wasn't a BPO request), show message: "No deliverables needed for this email"

**Data source:** `POST /api/process`

---

### 5. Pipeline

Full pipeline view across all BPO partners with deliverable tracking.

**Layout:**

**Top section — Partner summary cards:**
- One card per BPO partner (from `GET /api/pipeline`)
- Each card shows: Partner name, row count, deliverable completion bar (e.g., "18/36 demos, 12/36 CX Intel")
- Click a card → scrolls to or filters the table below for that partner

**Main section — Pipeline table:**
- Default: show all partners combined, or filtered by selected card
- Table columns: BPO Partner, Company, Stage, Date, Account Executive, Demo (checkmark/link), CX Intel (checkmark/link), Deep Dive (checkmark/link), Stakeholder Intel (checkmark/link), Presentation (checkmark/link)
- Deliverable columns show a green checkmark if `deliverable_status[type]` is true, with the cell linking to the actual file URL from the row data
- Empty deliverables show a gray dash
- Sortable by any column, searchable by company name

**Filters:**
- BPO partner dropdown
- Stage dropdown
- Deliverable completion (e.g., "Missing CX Intel", "All complete")

**Data sources:**
- `GET /api/pipeline` — summary and table data

---

### 6. Settings

Configuration and controls.

**Sections:**

**System Config** (read-only display from `GET /api/config`):
- Poll interval
- BPO domains list
- Slack channel
- DRY_RUN status

**BPO Partner Registry** (from config):
- Table showing each partner: Name, Domains, Drive Folder (link), Pipeline Sheet (link)
- Read-only for now

---

## Navigation

Sidebar navigation with:
1. **Dashboard** (home icon) — default page
2. **Approvals** (check-circle icon) — with badge count of pending approvals
3. **Pipeline** (bar-chart icon) — partner pipelines + deliverable tracking
4. **Sessions** (clock icon)
5. **Submit Request** (plus icon)
6. **Settings** (gear icon)

---

## Design Notes

- **Brand**: Anyreach brand. Primary color: `#6366f1` (indigo). Clean, professional, minimal.
- **Dark mode**: Support both light and dark mode
- **Responsive**: Desktop-first but should work on tablet
- **Empty states**: All lists should have helpful empty state messages
- **Loading states**: Skeleton loaders for cards and tables while data loads
- **Error handling**: Toast notifications for API errors. Retry button on failed loads.
- **Polling**: Dashboard and Approvals pages should poll for updates every 30 seconds
- **Timestamps**: Show relative time ("2 hours ago") with full timestamp on hover

---

## Environment Variables

The frontend needs one env variable:
```
VITE_API_BASE_URL=http://localhost:8080
VITE_API_TOKEN=change-me-in-production
```

All API calls should include:
```
Authorization: Bearer ${VITE_API_TOKEN}
Content-Type: application/json
```
