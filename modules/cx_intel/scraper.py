"""Multi-pass CX review scraper using Claude Sonnet with web search.

Phase A: Discover which platforms have reviews and their URLs.
Phase B: Scrape each platform in parallel (up to 7, semaphore=3).
Phase C: Extract themes from all aggregated reviews.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

from shared.anthropic_client import get_client

logger = logging.getLogger(__name__)

PLATFORMS = [
    "Trustpilot",
    "Google Reviews",
    "BBB",
    "ConsumerAffairs",
    "G2",
    "Glassdoor",
    "Indeed",
    "Yelp",
]

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

# ── JSON extraction helpers ──────────────────────────────────────────


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    fence_pattern = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?\s*```$", re.DOTALL)
    match = fence_pattern.match(stripped)
    if match:
        return match.group(1).strip()
    return stripped


def _extract_json(text: str) -> str:
    cleaned = _strip_code_fences(text)

    # Try to extract from embedded code fences (e.g. commentary + ```json [...] ```)
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", cleaned, re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    if cleaned.lstrip().startswith(("{", "[")):
        start_ch = "{" if cleaned.lstrip().startswith("{") else "["
        end_ch = "}" if start_ch == "{" else "]"
        depth = 0
        start = cleaned.index(start_ch)
        for i, ch in enumerate(cleaned[start:], start):
            if ch == start_ch:
                depth += 1
            elif ch == end_ch:
                depth -= 1
                if depth == 0:
                    return cleaned[start : i + 1]

    first = cleaned.find("[")
    last = cleaned.rfind("]")
    if first != -1 and last > first:
        return cleaned[first : last + 1]

    first = cleaned.find("{")
    last = cleaned.rfind("}")
    if first != -1 and last > first:
        return cleaned[first : last + 1]
    return cleaned


def _safe_json(text: str, fallback=None):
    try:
        return json.loads(_extract_json(text))
    except (json.JSONDecodeError, ValueError):
        return fallback


# ── Phase A: Discovery ───────────────────────────────────────────────

DISCOVERY_PROMPT = """\
Find review platforms for this company:

Company: {company_name}
Website: {company_url}

Search for "{company_name} reviews" and check each of these platforms:
{platform_list}

Return a JSON array (no markdown, no commentary). Each entry:
{{
  "platform": "Platform Name",
  "url": "https://... direct URL to the reviews page",
  "estimated_reviews": 150,
  "rating": 4.2,
  "has_reviews": true
}}

Only include platforms where you actually found reviews. If a platform has no reviews for this company, omit it.
Return ONLY the JSON array.
"""


async def discover_platforms(company_name: str, company_url: str, api_key: str) -> list[dict]:
    """Discover which platforms have reviews for this company."""
    client = get_client(api_key)
    platform_list = "\n".join(f"- {p}" for p in PLATFORMS)
    prompt = DISCOVERY_PROMPT.format(
        company_name=company_name,
        company_url=company_url or "N/A",
        platform_list=platform_list,
    )

    try:
        resp = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4000,
            system="You are a research analyst. Search the web to find review platforms for companies. Return structured JSON.",
            messages=[{"role": "user", "content": prompt}],
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
        )
    except Exception as e:
        logger.error("Discovery call failed: %s", e)
        return []

    text_parts = [b.text for b in resp.content if b.type == "text"]
    raw = "\n".join(text_parts)
    logger.debug("Discovery raw response for %s: %s", company_name, raw[:1000])
    platforms = _safe_json(raw, [])
    if not isinstance(platforms, list):
        logger.warning("Discovery response was not a list for %s, got: %s", company_name, type(platforms).__name__)
        platforms = []

    logger.info("Discovered %d review platforms for %s", len(platforms), company_name)
    return [p for p in platforms if p.get("has_reviews", True)]


# ── Phase B: Platform scraping ───────────────────────────────────────

PLATFORM_PROMPT = """\
Collect customer reviews from this specific platform:

Company: {company_name}
Platform: {platform}
URL: {url}

Go to the URL and collect AS MANY reviews as possible. Navigate pagination (page 2, 3, 4, 5+).
Target: collect at least 50 reviews, up to 100 if available.

For each review, extract:
- "text": the full review text (or first 200 characters if very long)
- "rating": numeric rating (1-5 scale) if shown
- "date": date of the review (YYYY-MM-DD format if possible)
- "author": reviewer name if shown
- "sentiment": "positive", "mixed", or "negative"

{employee_note}

Return a JSON object (no markdown):
{{
  "platform": "{platform}",
  "rating": 4.1,
  "reviews": [ ... array of review objects ... ],
  "total_available": 523
}}
"""


async def scrape_platform(
    company_name: str,
    platform: str,
    url: str,
    api_key: str,
    semaphore: asyncio.Semaphore,
) -> dict:
    """Scrape reviews from a single platform."""
    client = get_client(api_key)

    is_employee = platform.lower() in ("glassdoor", "indeed")
    employee_note = (
        'These are EMPLOYEE reviews. Also extract "title", "pros", and "cons" fields for each review.'
        if is_employee else ""
    )

    prompt = PLATFORM_PROMPT.format(
        company_name=company_name,
        platform=platform,
        url=url,
        employee_note=employee_note,
    )

    async with semaphore:
        try:
            resp = await client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=12000,
                system="You are a web scraping analyst. Collect reviews from the given platform URL. Be thorough — navigate multiple pages to collect as many reviews as possible.",
                messages=[{"role": "user", "content": prompt}],
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
            )
        except Exception as e:
            logger.error("Platform scrape failed for %s: %s", platform, e)
            return {"platform": platform, "rating": None, "reviews": [], "total_available": 0}

    text_parts = [b.text for b in resp.content if b.type == "text"]
    raw = "\n".join(text_parts)
    data = _safe_json(raw, {})
    if not isinstance(data, dict):
        data = {}

    reviews = data.get("reviews", [])
    logger.info("Scraped %d reviews from %s for %s", len(reviews), platform, company_name)

    return {
        "platform": platform,
        "rating": data.get("rating"),
        "reviews": reviews,
        "total_available": data.get("total_available", len(reviews)),
        "is_employee": is_employee,
    }


# ── Phase C: Theme extraction ────────────────────────────────────────

THEME_PROMPT = """\
Analyze these {review_count} customer/employee reviews for {company_name} across {platform_count} platforms.

Reviews summary (first 200 reviews shown):
{reviews_text}

Identify 5-15 recurring themes across all reviews. For each theme:
- "theme": short descriptive name
- "frequency": "high" (>20% of reviews), "medium" (10-20%), or "low" (<10%)
- "sentiment": "positive", "mixed", or "negative"
- "platforms": which platforms mention this theme

Also provide:
- "summary": 2-3 sentence overall CX assessment
- "overall_rating": weighted average across platforms (numeric)
- "sentiment_distribution": {{"positive": N, "mixed": N, "negative": N}} counts from all reviews

Return JSON:
{{
  "themes": [ ... ],
  "summary": "...",
  "overall_rating": 3.8,
  "sentiment_distribution": {{"positive": 120, "mixed": 45, "negative": 30}}
}}
"""


async def extract_themes(
    all_reviews: list[dict],
    company_name: str,
    platform_count: int,
    api_key: str,
) -> dict:
    """Extract themes and summary from aggregated reviews."""
    client = get_client(api_key)

    sample = all_reviews[:200]
    reviews_text = "\n".join(
        f"[{r.get('platform', '?')}] ({r.get('sentiment', '?')}) {r.get('text', '')[:200]}"
        for r in sample
    )

    prompt = THEME_PROMPT.format(
        review_count=len(all_reviews),
        company_name=company_name,
        platform_count=platform_count,
        reviews_text=reviews_text,
    )

    try:
        resp = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4000,
            system="You are a CX analytics expert. Analyze review data and identify themes.",
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        logger.error("Theme extraction failed: %s", e)
        return {"themes": [], "summary": "", "overall_rating": None, "sentiment_distribution": {}}

    raw = resp.content[0].text if resp.content else ""
    data = _safe_json(raw, {})
    if not isinstance(data, dict):
        data = {}

    return {
        "themes": data.get("themes", []),
        "summary": data.get("summary", ""),
        "overall_rating": data.get("overall_rating"),
        "sentiment_distribution": data.get("sentiment_distribution", {"positive": 0, "mixed": 0, "negative": 0}),
    }


# ── Main orchestrator ────────────────────────────────────────────────


async def scrape_reviews(company_name: str, company_url: str, api_key: str) -> dict:
    """Multi-pass review scraping: discover → parallel scrape → theme extraction.

    Returns the same dict schema as before for backward compatibility.
    """
    # Phase A: Discover platforms
    logger.info("Phase A: Discovering review platforms for %s", company_name)
    platforms = await discover_platforms(company_name, company_url, api_key)

    if not platforms:
        logger.warning("No review platforms found for %s", company_name)
        return dict(_EMPTY_DATA)

    # Phase B: Scrape each platform in parallel (max 3 concurrent)
    logger.info("Phase B: Scraping %d platforms for %s", len(platforms), company_name)
    semaphore = asyncio.Semaphore(3)
    tasks = [
        scrape_platform(company_name, p["platform"], p.get("url", ""), api_key, semaphore)
        for p in platforms
    ]
    platform_results = await asyncio.gather(*tasks, return_exceptions=True)

    # Aggregate results
    all_consumer_reviews: list[dict] = []
    all_employee_reviews: list[dict] = []
    ratings_summary: dict = {}

    for result in platform_results:
        if isinstance(result, Exception):
            logger.error("Platform scrape exception: %s", result)
            continue
        if not isinstance(result, dict):
            continue

        platform_name = result.get("platform", "Unknown")
        reviews = result.get("reviews", [])
        rating = result.get("rating")

        if rating is not None:
            ratings_summary[platform_name] = rating

        for review in reviews:
            review["platform"] = platform_name

        if result.get("is_employee"):
            all_employee_reviews.extend(reviews)
        else:
            all_consumer_reviews.extend(reviews)

    all_reviews = all_consumer_reviews + all_employee_reviews
    total_found = len(all_reviews)

    logger.info(
        "Phase B complete: %d consumer + %d employee = %d total reviews for %s",
        len(all_consumer_reviews), len(all_employee_reviews), total_found, company_name,
    )

    if not all_reviews:
        return dict(_EMPTY_DATA)

    # Phase C: Extract themes and summary
    logger.info("Phase C: Extracting themes from %d reviews for %s", total_found, company_name)
    theme_data = await extract_themes(all_reviews, company_name, len(ratings_summary), api_key)

    result = {
        "reviews": all_consumer_reviews,
        "themes": theme_data.get("themes", []),
        "ratings_summary": ratings_summary,
        "sentiment_distribution": theme_data.get("sentiment_distribution", {"positive": 0, "mixed": 0, "negative": 0}),
        "employee_reviews": all_employee_reviews,
        "overall_rating": theme_data.get("overall_rating"),
        "total_reviews_found": total_found,
        "summary": theme_data.get("summary", ""),
    }

    logger.info(
        "Scraping complete for %s: %d reviews, %d themes, rating=%s",
        company_name, total_found, len(result["themes"]), result["overall_rating"],
    )

    return result
