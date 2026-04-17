import os
from pathlib import Path

from slugify import slugify

TEMP_DIR = Path(os.environ.get("TEMP_DIR", "/tmp/bpo-ops"))


def ensure_session_dir(session_id: str) -> Path:
    d = TEMP_DIR / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def artifact_path(session_id: str, company: str, suffix: str, ext: str) -> Path:
    d = ensure_session_dir(session_id)
    slug = slugify(company, max_length=40)
    return d / f"{slug}_{suffix}.{ext}"
