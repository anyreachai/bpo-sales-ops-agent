from __future__ import annotations


STAKEHOLDER_SYSTEM = """\
You are a world-class competitive intelligence analyst preparing a stakeholder \
intelligence brief for an enterprise B2B sales team. Your research must be \
thorough, evidence-based, and actionable. You will be given a target contact \
at a company and must produce a comprehensive dossier.

Rules:
- Prioritize EVIDENCE over speculation. Cite sources where possible.
- If you cannot verify a claim, explicitly mark it as "unconfirmed" or "inferred."
- Use web search aggressively to find LinkedIn profiles, conference talks, press \
  releases, company news, alumni directories, and professional mentions.
- Write in a crisp, analytical tone — this is an intelligence brief, not a blog post.
- Use markdown formatting with clear section headers (## Section Name).
- Include bullet points for scannable data.
- For the Tactical Playbook section, provide SPECIFIC conversation starters, \
  topics to avoid, and recommended messaging angles based on your findings.\
"""


def build_stakeholder_prompt(
    contact_name: str,
    contact_title: str | None = None,
    company_name: str | None = None,
    company_url: str | None = None,
    bpo_context: str = "",
) -> str:
    """Build the user prompt for stakeholder intelligence research."""

    title_line = f"- **Title / Role**: {contact_title}" if contact_title else ""
    company_line = f"- **Company**: {company_name}" if company_name else ""
    url_line = f"- **Company URL**: {company_url}" if company_url else ""
    bpo_line = f"\n**BPO Partner Context**: {bpo_context}" if bpo_context else ""

    return f"""\
Research the following person and produce a comprehensive Stakeholder Intelligence Brief.

**Target Contact:**
- **Name**: {contact_name}
{title_line}
{company_line}
{url_line}
{bpo_line}

---

Use web search to investigate across ALL of these source categories:
1. **LinkedIn** — current role, past roles, tenure, education, certifications, skills, endorsements, post activity
2. **Alumni & education profiles** — university alumni directories, MBA cohort pages, scholarship mentions
3. **Conference appearances** — speaker bios, panel transcriptions, webinar announcements, event agendas
4. **News & press** — press releases, executive appointments, interviews, podcast appearances, bylined articles
5. **Company context** — recent earnings, M&A activity, layoffs, leadership changes, strategic pivots, tech stack signals
6. **Professional associations** — COPC, SOCAP, ICMI, CX Network, industry board memberships
7. **Social media & thought leadership** — Twitter/X posts, blog posts, Medium articles, industry forum contributions

---

Produce the brief with exactly these 8 sections. Use ## headings for each section:

## Career Arc
Map their full professional trajectory from earliest available data to present. Include:
- Each role with company, approximate dates, and scope (team size, revenue, geo)
- Key career inflection points (promotions, lateral moves, industry switches)
- Education background and any notable certifications (Six Sigma, PMP, COPC, etc.)
- Pattern analysis: are they a builder, optimizer, cost-cutter, or empire-builder?

## What They Control
Analyze their current sphere of influence:
- Budget authority (estimate range if possible based on company size and role)
- Team size and organizational structure beneath them
- Key technology platforms they likely own or influence
- Vendor relationships they likely manage
- KPIs they are measured on based on their role

## LinkedIn Intelligence
Summarize signals from their LinkedIn presence:
- Profile completeness and personal branding strength
- Posting frequency and topics they engage with
- Notable connections or group memberships
- Endorsements and recommendations themes
- Recent activity that signals priorities or frustrations

## Network & Orbit
Map their professional network:
- Former colleagues now at other companies (potential references or blockers)
- Conference co-panelists or co-authors
- Shared connections with our team or BPO partner contacts
- Industry circles they run in (associations, advisory boards, peer groups)
- Identify potential warm introduction paths

## Company Context & Timing
Analyze the company environment this person operates in:
- Recent company news (earnings, restructuring, new leadership, M&A)
- Current strategic priorities visible from public sources
- Technology stack signals (job postings, vendor announcements, case studies)
- Competitive dynamics — who are they losing deals or talent to?
- Timing signals — fiscal year, budget cycle, contract renewal windows

## Psychological Profile
Based on ALL available evidence, build a communication profile:
- Communication style (data-driven vs. relationship-driven, formal vs. casual)
- Decision-making pattern (consensus builder vs. decisive, risk-averse vs. bold)
- Career motivations (career advancement, operational excellence, innovation, cost savings)
- Likely objections or concerns based on their background
- Values and hot buttons inferred from public statements or career choices

## Tactical Playbook
Provide specific, actionable guidance for the sales team:
- **Opening angles**: 3 specific conversation starters tied to their background
- **Messaging framework**: What value propositions will resonate most based on their profile
- **Topics to lean into**: Subjects where they have passion or expertise
- **Topics to avoid**: Potential landmines based on their history
- **Proof points to prepare**: Case studies, data points, or references that align with their priorities
- **Meeting format recommendation**: In-person vs. virtual, formal presentation vs. working session
- **Champion vs. blocker assessment**: Likelihood they become an internal champion

## Conclusion
2-3 paragraph executive summary synthesizing the key findings and the single most important \
insight the sales team should carry into their first conversation with this person.

---

IMPORTANT: Start each section with the ## heading exactly as shown above. \
Under each section, use bullet points and sub-bullets for structured data. \
Use **bold** for emphasis on key findings. \
If you cannot find information for a specific area, state that explicitly rather than fabricating data.\
"""
