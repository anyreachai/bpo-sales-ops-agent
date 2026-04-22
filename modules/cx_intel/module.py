"""CX Intelligence module — scrapes reviews, generates XLSX + PDF deliverables."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from modules._base import BaseModule
from modules.cx_intel.scraper import scrape_reviews
from modules.cx_intel.xlsx_generator import generate_cx_xlsx
from modules.report_templates import render_pdf
from orchestrator.config import settings
from shared.storage import artifact_path
from shared.types import Artifact, ModuleResult, SessionContext

logger = logging.getLogger(__name__)


def _build_recommendations(themes: list[dict]) -> list[dict]:
    """Generate recommendation cards from negative/mixed themes."""
    recs = []
    for t in themes:
        if t.get("sentiment") in ("negative", "mixed") and t.get("theme"):
            recs.append({
                "title": t["theme"],
                "body": f"Reported on {', '.join(t.get('platforms', []))} with {t.get('frequency', 'moderate')} frequency. "
                        f"Addressing this theme could improve customer satisfaction.",
            })
    return recs[:6]


class CxIntelModule(BaseModule):
    name = "cx_intel"

    def should_run(self, ctx: SessionContext) -> bool:
        return "cx_intel" in ctx.deliverables_requested

    async def run(self, ctx: SessionContext) -> ModuleResult:
        company = ctx.target_company or "Unknown Company"
        url = ctx.target_url or ""

        logger.info("Scraping reviews for %s (%s)", company, url)
        review_data = await scrape_reviews(
            company_name=company,
            company_url=url,
            api_key=settings.ANTHROPIC_API_KEY,
        )

        total_found = review_data.get("total_reviews_found", 0)
        logger.info("Scraper returned %d total reviews for %s", total_found, company)

        artifacts: list[Artifact] = []

        # 1. Generate XLSX
        xlsx_path = artifact_path(ctx.session_id, company, "cx_intel", "xlsx")
        try:
            generate_cx_xlsx(review_data, company, xlsx_path)
            size = xlsx_path.stat().st_size if xlsx_path.exists() else 0
            artifacts.append(Artifact(
                filename=xlsx_path.name,
                path=xlsx_path,
                artifact_type="cx_intel_xlsx",
                mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                size_bytes=size,
            ))
            logger.info("XLSX generated: %s (%d bytes)", xlsx_path, size)
        except Exception as e:
            logger.error("Failed to generate XLSX: %s", e, exc_info=True)

        # 2. Generate PDF via HTML template
        pdf_path = artifact_path(ctx.session_id, company, "cx_intel", "pdf")
        try:
            sentiment = review_data.get("sentiment_distribution", {})
            total_sentiment = sum(sentiment.values()) or 1
            positive_pct = round(100 * sentiment.get("positive", 0) / total_sentiment)
            mixed_pct = round(100 * sentiment.get("mixed", 0) / total_sentiment)
            negative_pct = round(100 * sentiment.get("negative", 0) / total_sentiment)

            sentiment_counts = {
                "positive": sentiment.get("positive", 0),
                "mixed": sentiment.get("mixed", 0),
                "negative": sentiment.get("negative", 0),
                "positive_pct": positive_pct,
                "mixed_pct": mixed_pct,
                "negative_pct": negative_pct,
            }

            render_pdf(
                template_name="cx_intel.html",
                context={
                    "company_name": company,
                    "date": datetime.now(timezone.utc).strftime("%B %d, %Y"),
                    "overall_rating": review_data.get("overall_rating", "N/A"),
                    "total_reviews": total_found,
                    "positive_pct": positive_pct,
                    "platform_count": len(review_data.get("ratings_summary", {})),
                    "summary": review_data.get("summary", ""),
                    "ratings_summary": review_data.get("ratings_summary", {}),
                    "sentiment_counts": sentiment_counts,
                    "themes": review_data.get("themes", []),
                    "reviews": review_data.get("reviews", []),
                    "employee_reviews": review_data.get("employee_reviews", []),
                    "recommendations": _build_recommendations(review_data.get("themes", [])),
                },
                output_path=pdf_path,
                brand_guide=ctx.brand_guide,
            )
            size = pdf_path.stat().st_size if pdf_path.exists() else 0
            artifacts.append(Artifact(
                filename=pdf_path.name,
                path=pdf_path,
                artifact_type="cx_intel_pdf",
                mime_type="application/pdf",
                size_bytes=size,
            ))
            logger.info("PDF generated: %s (%d bytes)", pdf_path, size)
        except Exception as e:
            logger.error("Failed to generate PDF: %s", e, exc_info=True)

        if not artifacts:
            return ModuleResult(
                module_name=self.name,
                status="failed",
                error="Both XLSX and PDF generation failed",
                metadata={"total_reviews_found": total_found},
            )

        return ModuleResult(
            module_name=self.name,
            status="success",
            artifacts=artifacts,
            metadata={
                "total_reviews_found": total_found,
                "review_count": len(review_data.get("reviews", [])),
                "employee_review_count": len(review_data.get("employee_reviews", [])),
                "theme_count": len(review_data.get("themes", [])),
                "platforms_with_ratings": list(review_data.get("ratings_summary", {}).keys()),
                "overall_rating": review_data.get("overall_rating"),
                "summary": review_data.get("summary", ""),
            },
        )
