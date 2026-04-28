from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class EmailPayload(BaseModel):
    from_address: str
    subject: str
    body: str
    message_id: str | None = None
    cc: list[str] = []


class BPOPartner(BaseModel):
    key: str
    name: str
    domains: list[str]
    drive_folder_id: str
    pipeline_sheet_id: str | None = None
    key_contacts: list[str] = []
    slack_channel: str | None = None
    attio_record_id: str | None = None


class IntakeAnswers(BaseModel):
    contact_name: str | None = None
    contact_title: str | None = None
    target_business_area: str | None = None
    pain_points: str | None = None
    current_setup: str | None = None


class Artifact(BaseModel):
    filename: str
    path: Path
    artifact_type: Literal[
        "deep_research", "stakeholder_intel",
        "cx_intel_xlsx", "cx_intel_pdf",
        "pitch_deck", "brand_guide", "demo_link_doc",
    ]
    mime_type: str
    size_bytes: int = 0

    class Config:
        arbitrary_types_allowed = True


class ModuleResult(BaseModel):
    module_name: str
    status: Literal["success", "failed", "skipped"]
    artifacts: list[Artifact] = []
    metadata: dict = {}
    duration_seconds: float = 0.0
    error: str | None = None

    class Config:
        arbitrary_types_allowed = True


class SessionContext(BaseModel):
    session_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now())
    status: str = "received"

    raw_email: EmailPayload

    bpo: BPOPartner | None = None
    target_company: str | None = None
    target_url: str | None = None
    deliverables_requested: list[str] = []
    intake: IntakeAnswers | None = None

    module_results: dict[str, ModuleResult] = {}

    brand_guide: dict | None = None
    demo_link: str | None = None
    all_artifacts: list[Artifact] = []
    drive_links: dict[str, str] = {}

    dry_run: bool = False

    class Config:
        arbitrary_types_allowed = True
