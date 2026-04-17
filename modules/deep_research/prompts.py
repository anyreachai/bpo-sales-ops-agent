from __future__ import annotations


def build_research_prompt(
    company_name: str,
    company_url: str | None = None,
    intake: dict | None = None,
    bpo_context: str = "",
) -> str:
    """Build the deep-research prompt for Claude Opus with web search.

    The prompt instructs Claude to produce an 8-section research report with
    15-30 web searches, evidence-backed claims, data tables, and a candid
    strategic assessment.
    """
    url_line = f"- **Website**: {company_url}" if company_url else ""

    intake_block = ""
    if intake:
        intake_lines = []
        if intake.get("contact_name"):
            intake_lines.append(f"- Contact: {intake['contact_name']}" +
                                (f" ({intake['contact_title']})" if intake.get("contact_title") else ""))
        if intake.get("target_business_area"):
            intake_lines.append(f"- Target Business Area: {intake['target_business_area']}")
        if intake.get("pain_points"):
            intake_lines.append(f"- Known Pain Points: {intake['pain_points']}")
        if intake.get("current_setup"):
            intake_lines.append(f"- Current Setup: {intake['current_setup']}")
        if intake_lines:
            intake_block = "\n### Intake Context\n" + "\n".join(intake_lines)

    bpo_block = ""
    if bpo_context:
        bpo_block = f"\n### BPO Partner Context\n{bpo_context}"

    return f"""\
You are a senior corporate intelligence analyst preparing a deep research report
on **{company_name}** for a BPO sales team. This report will be used to craft a
highly personalized sales approach.

## Target Company
- **Name**: {company_name}
{url_line}
{intake_block}
{bpo_block}

## Instructions

Conduct thorough research on {company_name} using **15 to 30 web searches**.
Search for:
- Official company website, about/leadership pages, investor relations
- Recent press releases, news articles, and earnings calls (last 12 months)
- LinkedIn company profile, employee count trends, recent hires
- Glassdoor/Indeed reviews for customer service culture signals
- SEC filings, annual reports, or funding rounds
- Acquisitions, mergers, divestitures, or strategic partnerships
- Industry analyst reports or rankings mentioning the company
- Social media presence and brand sentiment
- Technology stack (job postings are a great signal)
- Any existing BPO, outsourcing, or contact center partnerships
- Customer complaints, BBB records, regulatory actions
- Competitors in their space and market positioning

## Output Format

Produce a **detailed markdown report** with exactly these 8 sections. Use ## for
section headers and ### for subsections. Include data tables where appropriate
(using markdown table syntax with | delimiters).

### 1. Executive Summary
A concise 3-5 paragraph overview covering: what the company does, scale and market
position, key recent developments, and the strategic opportunity for BPO engagement.
End with a candid 1-paragraph assessment of deal viability.

### 2. Company Overview & History
- Founded date, headquarters, legal entity type
- Mission/vision statement
- Key milestones and timeline of major events
- Current scale: revenue, employees, locations, market cap (if public)
- Include a markdown table: | Metric | Value | Source |

### 3. Corporate Structure
- Parent company / subsidiary relationships
- Business units and divisions
- Geographic footprint (domestic and international)
- Key subsidiaries or brands
- Include an org-hierarchy description if discoverable

### 4. Division Deep Dives
For each major business unit or product line:
- What it does, its revenue contribution (if available)
- Customer base and target market
- Growth trajectory
- How customer service / support is delivered today
- Potential BPO needs

### 5. Go-to-Market Analysis
- Primary customer segments and verticals served
- Sales model (direct, channel, marketplace, etc.)
- Pricing strategy signals
- Marketing channels and brand voice
- Key partnerships and alliances
- Competitive positioning vs. top 3-5 competitors
- Include a competitor comparison table: | Competitor | Est. Revenue | Key Differentiator |

### 6. Key Personnel
Identify the people most relevant to a BPO sales engagement:
- C-suite (CEO, COO, CFO, CTO)
- VP/SVP of Customer Experience, Operations, or Contact Center
- VP of Procurement or Vendor Management
- Any recent leadership changes (hires, departures)
- Include a markdown table: | Name | Title | LinkedIn (if found) | Relevance |

### 7. Challenges & Strategic Implications
- Current business challenges (from earnings calls, news, Glassdoor)
- Technology transformation initiatives
- Regulatory or compliance pressures
- Customer satisfaction trends
- Cost optimization pressures
- Where BPO / AI-powered CX could address pain points
- Candid assessment: what would make them buy, what would make them say no

### 8. Conclusion
- Recommended approach angle for the BPO sales team
- Suggested talking points tied to their specific challenges
- Potential deal size estimation (small / mid-market / enterprise)
- Acquisition timeline recommendation (markdown table):
  | Phase | Action | Timeline |
  |-------|--------|----------|
  | Discovery | ... | Week 1-2 |
  | ... | ... | ... |

## Quality Standards
- Every factual claim must be backed by evidence found via web search.
- If information is unavailable, say so explicitly rather than speculating.
- Use a professional but candid tone. The audience is an internal sales team
  that values honest assessments over hype.
- Format all financial figures with appropriate units ($M, $B).
- Include dates for all time-sensitive information.
- Minimum report length: 2,500 words.
"""
