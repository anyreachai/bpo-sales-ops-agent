"""Scrape customer and employee reviews using Claude Sonnet with web search."""

from __future__ import annotations

import json
import logging
import re

from shared.anthropic_client import get_client

logger = logging.getLogger(__name__)

SCRAPER_SYSTEM = (
    "You are a CX intelligence analyst. Your job is to search the web for customer "
    "and employee reviews of a given company, then return structured JSON. "
    "Use the web_search tool to find reviews on every platform listed in the prompt. "
    "Be thorough: search each platform individually if needed. "
    "Always return valid JSON matching the exact schema requested."
)

SCRAPER_PROMPT_TEMPLATE = """\
Research the customer experience and employee sentiment for:

Company: {company_name}
Website: {company_url}

Search ALL of these platforms for reviews:
1. Trustpilot
2. Google Reviews / Google Maps
3. BBB (Better Business Bureau)
4. ConsumerAffairs
5. G2
6. Glassdoor (employee reviews)
7. Indeed (employee reviews)

For each platform, search for "{company_name} reviews [platform]".

After collecting all available data, return a single JSON object with this EXACT schema \
(no markdown, no commentary outside the JSON):

{{
  "reviews": [
    {{
      "platform": "Trustpilot",
      "rating": 4.2,
      "text": "Short excerpt or summary of the review",
      "date": "2025-01-15",
      "sentiment": "positive"
    }}
  ],
  "themes": [
    {{
      "theme": "Long wait times",
      "frequency": "high",
      "sentiment": "negative",
      "platforms": ["Trustpilot", "Google Reviews"]
    }}
  ],
  "ratings_summary": {{
    "Trustpilot": 3.8,
    "Google Reviews": 4.1,
    "BBB": "A+",
    "ConsumerAffairs": 3.5,
    "G2": 4.3,
    "Glassdoor": 3.6,
    "Indeed": 3.4
  }},
  "sentiment_distribution": {{
    "positive": 42,
    "mixed": 18,
    "negative": 12
  }},
  "employee_reviews": [
    {{
      "platform": "Glassdoor",
      "rating": 3.5,
      "title": "Good benefits, poor management",
      "pros": "Benefits, remote work",
      "cons": "Management turnover, low pay",
      "date": "2025-03-10",
      "sentiment": "mixed"
    }}
  ],
  "overall_rating": 3.7,
  "total_reviews_found": 72,
  "summary": "Brief 2-3 sentence overall assessment"
}}

Rules:
- Only include platforms where you actually found reviews.
- If a platform has no reviews, omit it from ratings_summary.
- Collect up to 15 consumer reviews and 10 employee reviews (prioritize recent and varied).
- For themes, identify 3-8 recurring patterns across all reviews.
- sentiment must be one of: "positive", "mixed", "negative".
- frequency must be one of: "high", "medium", "low".
- Return ONLY the JSON object. No markdown code fences, no explanation before or after.
"""


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences (```json ... ```) if present."""
    stripped = text.strip()
    fence_pattern = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?\s*```$", re.DOTALL)
    match = fence_pattern.match(stripped)
    if match:
        return match.group(1).strip()
    return stripped


def _extract_json_object(text: str) -> str:
    """Try to extract the first JSON object from text, handling surrounding prose."""
    # First try stripping code fences
    cleaned = _strip_code_fences(text)

    # If it starts with { we might be good
    if cleaned.lstrip().startswith("{"):
        # Find the matching closing brace
        depth = 0
        start = cleaned.index("{")
        for i, ch in enumerate(cleaned[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return cleaned[start : i + 1]

    # Fallback: find first { and last }
    first_brace = cleaned.find("{")
    last_brace = cleaned.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        return cleaned[first_brace : last_brace + 1]

    return cleaned


_EMPTY_DATA: dict = {
    "reviews": [],
    "themes": [],
    "ratings_summary": {},
    "sentiment_distribution": {"positive": 0, "mixed": 0, "negative": 0},
    "employee_reviews": [],
    "overall_rating": None,
    "total_reviews_found": 0,
    "summary": "No reviews found for this company.",
}


async def scrape_reviews(company_name: str, company_url: str, api_key: str) -> dict:
    """Use Claude Sonnet with web search to scrape reviews for a company.

    Returns a dict with keys: reviews, themes, ratings_summary,
    sentiment_distribution, employee_reviews, overall_rating,
    total_reviews_found, summary.
    """
    client = get_client(api_key)

    prompt = SCRAPER_PROMPT_TEMPLATE.format(
        company_name=company_name,
        company_url=company_url or "N/A",
    )

    try:
        resp = await client.messages.create(
            model="claude-sonnet-4-6-20250514",
            max_tokens=8000,
            system=SCRAPER_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
        )
    except Exception as e:
        logger.error("Anthropic API call failed during review scraping: %s", e)
        return dict(_EMPTY_DATA, summary=f"Scraping failed: {e}")

    # Extract text blocks from the response (skip tool_use and thinking blocks)
    text_parts = [block.text for block in resp.content if block.type == "text"]
    raw_text = "\n".join(text_parts)

    if not raw_text.strip():
        logger.warning("Claude returned no text content during scraping")
        return dict(_EMPTY_DATA)

    # Parse JSON from response
    json_str = _extract_json_object(raw_text)
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse scraper JSON: %s\nRaw text: %s", e, raw_text[:2000])
        return dict(_EMPTY_DATA, summary=f"JSON parse error: {e}")

    # Ensure all expected keys exist with defaults
    result = {
        "reviews": data.get("reviews", []),
        "themes": data.get("themes", []),
        "ratings_summary": data.get("ratings_summary", {}),
        "sentiment_distribution": data.get("sentiment_distribution", {"positive": 0, "mixed": 0, "negative": 0}),
        "employee_reviews": data.get("employee_reviews", []),
        "overall_rating": data.get("overall_rating"),
        "total_reviews_found": data.get("total_reviews_found", 0),
        "summary": data.get("summary", ""),
    }

    logger.info(
        "Scraped %d reviews, %d employee reviews, %d themes for %s",
        len(result["reviews"]),
        len(result["employee_reviews"]),
        len(result["themes"]),
        company_name,
    )

    return result
