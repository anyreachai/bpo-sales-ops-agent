"""Reusable mock return values for all external service calls."""

import json
import base64

# ── Anthropic API mocks ────────────────────────────────────────────────

MOCK_CLASSIFICATION_RESPONSE = '''```json
{
  "target_company": "GameStop",
  "target_url": "https://gamestop.com",
  "deliverables": ["demo", "deep_research", "stakeholder_intel", "cx_intel", "pitch_deck"],
  "contact_name": "Jane Smith",
  "contact_title": "VP of Customer Experience",
  "business_area": "Customer Support",
  "pain_points": "Long hold times, high agent attrition",
  "current_setup": "Genesys Cloud CX with Salesforce Service Cloud",
  "intake_complete": true,
  "confidence": "high",
  "notes": null
}
```'''

MOCK_CLASSIFICATION_EVERYTHING = '''```json
{
  "target_company": "Lululemon",
  "target_url": "https://lululemon.com",
  "deliverables": ["everything"],
  "contact_name": "Test Contact",
  "contact_title": "Director of Operations",
  "business_area": "Customer Service",
  "pain_points": "Poor NPS scores",
  "current_setup": "Five9",
  "intake_complete": true,
  "confidence": "high",
  "notes": null
}
```'''

MOCK_CLASSIFICATION_MINIMAL = json.dumps({
    "target_company": "Wayfair",
    "target_url": "https://wayfair.com",
    "deliverables": ["cx_intel"],
    "contact_name": None,
    "contact_title": None,
    "business_area": None,
    "pain_points": None,
    "current_setup": None,
    "intake_complete": False,
    "confidence": "medium",
    "notes": "Only CX intel requested"
})

# Must be >200 chars for deep_research module validation
MOCK_RESEARCH_MARKDOWN = """## Executive Summary

GameStop Corporation is a leading specialty retailer of video games, consumer electronics, and gaming merchandise. The company operates approximately 4,400 stores across 10 countries.

## Company Overview & History

Founded in 1984 as Babbage's, the company has undergone significant transformation. Key milestones include the 2004 merger with EB Games and the 2021 retail investor phenomenon.

| Year | Event | Significance |
|------|-------|-------------|
| 1984 | Founded as Babbage's | Original founding |
| 2004 | Merger with EB Games | Became largest game retailer |
| 2021 | Meme stock event | Cultural phenomenon |

## Key Personnel

| Name | Title | Background |
|------|-------|-----------|
| Ryan Cohen | Chairman | Co-founder of Chewy |
| Matt Furlong | CEO | Former Amazon exec |

## Challenges & Strategic Implications

The company faces significant headwinds from digital distribution. Physical retail for games is declining at approximately 15% annually. The trade-in program remains a differentiator but margins are compressed.

## Conclusion

GameStop presents both challenges and opportunities for AI-powered customer experience solutions, particularly around the high-volume trade-in and customer support workflows.
"""

# Must be >100 chars for stakeholder_intel module validation
MOCK_STAKEHOLDER_MARKDOWN = """## Career Arc

Jane Smith has served as VP of Customer Experience at GameStop since 2022, following a 7-year tenure at Best Buy where she led the Geek Squad transformation initiative.

## What They Control

- Annual CX budget: estimated $45M
- 2,500+ customer service agents across 3 contact centers
- Technology stack decisions for customer-facing platforms

## LinkedIn Intelligence

Active poster focusing on CX transformation, AI in retail, and employee engagement topics. Uses hashtags like #CustomerExperience #RetailTech #AIinCX.

## Network & Orbit

| Company | Signal |
|---------|--------|
| Best Buy | Former employer, still connected |
| Salesforce | Frequent conference attendee |
| NICE | Evaluating for CCaaS |

## Company Context & Timing

GameStop is in the midst of a digital transformation push. Q2 earnings showed a 12% decline in foot traffic, making CX efficiency a board-level priority.

## Psychological Profile

**Type:** Analytical Visionary
**Decision Drivers:** Data-first, ROI-focused, values innovation but demands proof
**Risk Tolerance:** Moderate — will pilot but needs clear success metrics
**Communication Style:** Direct, appreciates structured presentations

## Tactical Playbook

**Language to use:** ROI, efficiency metrics, agent satisfaction scores
**Language to avoid:** Buzzwords without backing data, "disruption"
**Opening move:** Lead with the CX intel data showing their review sentiment trends
**Proof points:** Best Buy case study, similar retail deployments

## Conclusion

Jane Smith is a data-driven executive who will respond best to evidence-based pitches with clear ROI projections.
"""

MOCK_REVIEW_DATA = {
    "reviews": [
        {
            "platform": "Trustpilot",
            "rating": 2.5,
            "text": "Waited 45 minutes on hold for a simple return. Staff was friendly once I got through.",
            "date": "2026-03-15",
            "sentiment": "negative",
        },
        {
            "platform": "Google Reviews",
            "rating": 4.0,
            "text": "Great selection and the trade-in process was smooth. Would recommend.",
            "date": "2026-03-20",
            "sentiment": "positive",
        },
    ],
    "themes": [
        {"theme": "Long wait times", "frequency": "high", "sentiment": "negative", "platforms": ["Trustpilot", "Google Reviews"]},
        {"theme": "Knowledgeable staff", "frequency": "medium", "sentiment": "positive", "platforms": ["Google Reviews"]},
    ],
    "ratings_summary": {
        "Trustpilot": 2.5,
        "Google Reviews": 3.8,
        "BBB": 1.5,
    },
    "sentiment_distribution": {"positive": 35, "mixed": 25, "negative": 40},
    "employee_reviews": [
        {
            "platform": "Glassdoor",
            "rating": 3.2,
            "text": "Fun environment but low pay and limited growth opportunities.",
            "date": "2026-02-10",
            "sentiment": "mixed",
        },
    ],
    "overall_rating": 3.1,
    "total_reviews_found": 42,
    "summary": "Mixed customer sentiment with significant pain points around wait times and returns process.",
}

# Wrapped in code fences like Claude would return
MOCK_SCRAPER_RAW_JSON = "```json\n" + json.dumps(MOCK_REVIEW_DATA) + "\n```"

MOCK_SLIDE_JSON = json.dumps([
    {"slide_number": 1, "type": "title", "heading": "GameStop + Anyreach", "subtitle": "AI-Powered Customer Experience", "subtext": "Prepared for ResultsCX"},
    {"slide_number": 2, "type": "content", "heading": "The CX Challenge", "bullets": ["45-minute average hold times", "NPS score declining 8 points YoY", "Agent attrition at 62% annually"]},
    {"slide_number": 3, "type": "content", "heading": "Market Context", "bullets": ["Retail CX spending up 23%", "AI adoption accelerating in sector", "Competitors investing heavily"]},
    {"slide_number": 4, "type": "content", "heading": "Voice of the Customer", "bullets": ["Long wait times: 34 mentions", "Knowledgeable staff: 28 mentions", "Trade-in process praised"]},
    {"slide_number": 5, "type": "content", "heading": "The Anyreach Solution", "bullets": ["AI voice agents for tier-1 support", "Seamless escalation to human agents", "Real-time sentiment monitoring"]},
    {"slide_number": 6, "type": "content", "heading": "How It Works", "bullets": ["Deploy in 2 weeks", "Integrates with Genesys Cloud CX", "No disruption to existing workflows"]},
    {"slide_number": 7, "type": "stats_grid", "heading": "Results & Proof Points", "stats": [{"number": "73%", "label": "Reduction in hold times"}, {"number": "4.2x", "label": "ROI in first quarter"}, {"number": "91%", "label": "Customer satisfaction"}]},
    {"slide_number": 8, "type": "content", "heading": "Implementation Approach", "bullets": ["Week 1-2: Integration setup", "Week 3-4: Pilot with 50 agents", "Month 2-3: Full rollout"]},
    {"slide_number": 9, "type": "content", "heading": "Why Anyreach + ResultsCX", "bullets": ["Deep BPO domain expertise", "Proven enterprise deployments", "Dedicated success team"]},
    {"slide_number": 10, "type": "cta", "heading": "Next Steps", "bullets": ["Schedule technical deep-dive", "Define pilot scope and KPIs", "Begin integration planning"], "contact": "Richard Lin, CEO | richard@anyreach.ai"},
])

MOCK_EMAIL_BODY = "<html><body><p>Hi Jordan,</p><p>Thank you for your request regarding GameStop. We've prepared the following deliverables:</p><ul><li>Deep Research Report</li><li>Stakeholder Intelligence Brief</li><li>CX Intelligence Report</li><li>Pitch Deck</li></ul><p>All files are available in your shared Drive folder.</p><p>Best regards,<br>Richard Lin<br>CEO, Anyreach</p></body></html>"

# ── Brand.dev API mock ─────────────────────────────────────────────────

MOCK_BRAND_DATA = {
    "name": "GameStop",
    "colors": {
        "primary": "#FF0000",
        "secondary": "#000000",
        "accent": "#FFFFFF",
    },
    "logos": {
        "full": "https://brand.dev/logos/gamestop-full.png",
        "icon": "https://brand.dev/logos/gamestop-icon.png",
    },
    "fonts": {
        "heading": "Roboto",
        "body": "Open Sans",
    },
    "description": "GameStop is the world's largest retail gaming destination.",
}

# ── Google API mocks ───────────────────────────────────────────────────

MOCK_GMAIL_LIST_RESPONSE = {
    "messages": [
        {"id": "msg_001", "threadId": "thread_001"},
        {"id": "msg_002", "threadId": "thread_002"},
    ],
}

MOCK_GMAIL_LIST_EMPTY = {}  # Gmail returns no "messages" key when empty

_PLAIN_BODY = base64.urlsafe_b64encode(
    b"Hi Richard,\n\nCan you set up a demo for GameStop?\n\nThanks,\nJordan"
).decode()

MOCK_GMAIL_MESSAGE_DETAIL = {
    "id": "msg_001",
    "threadId": "thread_001",
    "payload": {
        "mimeType": "text/plain",
        "headers": [
            {"name": "From", "value": "jarmstrong@resultscx.com"},
            {"name": "Subject", "value": "GameStop demo request"},
            {"name": "Cc", "value": ""},
        ],
        "body": {"data": _PLAIN_BODY},
    },
}

_CALENDAR_BODY = base64.urlsafe_b64encode(b"Calendar invite details").decode()

MOCK_GMAIL_CALENDAR_INVITE = {
    "id": "msg_cal_001",
    "threadId": "thread_cal_001",
    "payload": {
        "mimeType": "text/plain",
        "headers": [
            {"name": "From", "value": "someone@startek.com"},
            {"name": "Subject", "value": "Accepted: Weekly sync with Richard"},
            {"name": "Cc", "value": ""},
        ],
        "body": {"data": _CALENDAR_BODY},
    },
}

MOCK_DRIVE_FOLDER_FOUND = {
    "files": [{"id": "folder_existing_123", "name": "GameStop"}],
}

MOCK_DRIVE_FOLDER_EMPTY = {"files": []}

MOCK_DRIVE_CREATE_RESPONSE = {
    "id": "folder_new_456",
    "webViewLink": "https://drive.google.com/drive/folders/folder_new_456",
}

MOCK_DRIVE_UPLOAD_RESPONSE = {
    "id": "file_uploaded_789",
    "webViewLink": "https://drive.google.com/file/d/file_uploaded_789/view",
}

MOCK_SHEETS_READ_RESPONSE = {
    "values": [
        ["Date", "Stage", "Type", "Company", "BPO Stakeholder", "Latest News", "URL", "Demo Link", "CX Intel", "Drive Folder"],
        ["2026-04-01", "Received", "New", "Lululemon", "Test User", "", "lululemon.com", "", "", ""],
    ],
}

MOCK_SHEETS_APPEND_RESPONSE = {"updates": {"updatedRows": 1}}

MOCK_SLACK_OK_RESPONSE = {"ok": True, "ts": "1713340800.123456"}

MOCK_GMAIL_DRAFT_RESPONSE = {"id": "draft_001", "message": {"id": "msg_draft_001"}}

# ── Fake PNG bytes (valid minimal header) ──────────────────────────────
FAKE_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
