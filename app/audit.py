import csv

from .config import ROOT
from .models import Draft, now_iso

AUDIT_PATH = ROOT / "reports/editorial_audit.csv"
FIELDS = [
    "draft_id", "topic", "title", "abstract", "source_urls", "draft_file",
    "created_at", "decision", "decision_timestamp", "publication_status",
    "publication_method", "linkedin_post_id", "publish_error", "trace_id",
    "infographic_status", "infographic_file", "infographic_qa", "infographic_error",
]


def record(draft: Draft, method: str = "") -> None:
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    exists = AUDIT_PATH.exists()
    if exists:
        with AUDIT_PATH.open("r", newline="", encoding="utf-8") as handle:
            existing_fields = next(csv.reader(handle), [])
        if existing_fields != FIELDS:
            rows: list[dict[str, str]] = []
            with AUDIT_PATH.open("r", newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            with AUDIT_PATH.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=FIELDS)
                writer.writeheader()
                for row in rows:
                    writer.writerow({field: row.get(field, "") for field in FIELDS})
    with AUDIT_PATH.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow({
            "draft_id": draft.draft_id,
            "topic": draft.topic_name,
            "title": draft.title,
            "abstract": draft.abstract,
            "source_urls": " | ".join(source.url for source in draft.sources),
            "draft_file": str(ROOT / "drafts" / f"{draft.draft_id}.txt"),
            "created_at": draft.created_at,
            "decision": draft.decision,
            "decision_timestamp": draft.approved_at or now_iso(),
            "publication_status": draft.publication_status,
            "publication_method": method,
            "linkedin_post_id": draft.linkedin_post_id or "",
            "publish_error": draft.publish_error or "",
            "trace_id": draft.trace_id or "",
            "infographic_status": draft.infographic_status,
            "infographic_file": draft.infographic_path or "",
            "infographic_qa": draft.infographic_qa.status if draft.infographic_qa else "",
            "infographic_error": draft.infographic_error or "",
        })
