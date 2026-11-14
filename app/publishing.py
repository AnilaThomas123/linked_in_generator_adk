import shutil
from datetime import UTC, datetime
from pathlib import Path

from .audit import record
from .config import ROOT
from .models import Draft


def publish_after_approval(draft: Draft) -> Draft:
    if draft.decision != "APPROVED":
        raise ValueError("A draft must be explicitly approved before publishing")
    # Manual publishing is the selected MVP path. The user must publish in LinkedIn
    # and then explicitly confirm completion in the dashboard.
    draft.publish_error = None
    draft.publication_status = "MANUAL_REQUIRED"
    write_ready_to_post(draft)
    record(draft, method="manual_publish_requested")
    return draft


def write_ready_to_post(draft: Draft) -> Path:
    """Write the post, first comment, and image into one folder for manual copying."""
    target = ROOT / "ready_to_post" / draft.draft_id
    target.mkdir(parents=True, exist_ok=True)
    first_comment = draft.first_comment or (
        f"Source: {draft.sources[0].title} ({draft.sources[0].publisher})\n{draft.sources[0].url}"
        if draft.sources else ""
    )
    (target / "1_post.txt").write_text(draft.content.strip() + "\n", encoding="utf-8")
    (target / "2_first_comment.txt").write_text(first_comment.strip() + "\n", encoding="utf-8")
    if draft.infographic_status == "READY" and draft.infographic_path:
        shutil.copyfile(ROOT / draft.infographic_path, target / "3_infographic.png")
    (target / "README.txt").write_text(
        "Manual LinkedIn steps\n"
        "1. Open LinkedIn and start a new post from your own account.\n"
        "2. Paste 1_post.txt as the post text.\n"
        "3. Attach 3_infographic.png, if it is present.\n"
        "4. Publish the post.\n"
        "5. Paste 2_first_comment.txt as the first comment.\n"
        "6. Return to the dashboard and confirm the publication.\n",
        encoding="utf-8",
    )
    draft.ready_to_post_dir = str(target.relative_to(ROOT))
    return target


def confirm_manual_publish(draft: Draft, linkedin_post_id: str = "") -> Draft:
    if draft.decision != "APPROVED":
        raise ValueError("Only an approved draft can be confirmed as published")
    draft.publication_status = "MANUALLY_PUBLISHED"
    draft.published_at = datetime.now(UTC).isoformat()
    draft.linkedin_post_id = linkedin_post_id or None
    write_published_pdf(draft)
    record(draft, method="manual_confirmation")
    return draft


def write_published_pdf(draft: Draft) -> Path:
    target = ROOT / "published_pdfs" / f"{draft.draft_id}_published.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"Draft ID: {draft.draft_id}", f"Topic: {draft.topic_name}",
        f"Decision: {draft.decision}", f"Publication status: {draft.publication_status}",
        f"Trace ID: {draft.trace_id or 'n/a'}",
        f"Infographic status: {draft.infographic_status}",
        f"Infographic file: {draft.infographic_path or 'n/a'}",
        f"Infographic QA: {draft.infographic_qa.status if draft.infographic_qa else 'n/a'}", "",
        draft.title, "", draft.content, "",
        "Sources:", *[source.url for source in draft.sources],
    ]
    wrapped = []
    for line in lines:
        while len(line) > 88:
            wrapped.append(line[:88])
            line = line[88:]
        wrapped.append(line)
    stream = "BT\\n/F1 10 Tf\\n48 750 Td\\n12 TL\\n" + "\\n".join(
        f"({line.replace(chr(92), chr(92) * 2).replace('(', chr(92) + '(').replace(')', chr(92) + ')')}) Tj T*"
        for line in wrapped
    ) + "\\nET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        f"<< /Length {len(stream.encode('latin-1', 'replace'))} >>\\nstream\\n{stream}\\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    pdf = bytearray(b"%PDF-1.4\\n%\\xe2\\xe3\\xcf\\xd3\\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(pdf)); pdf.extend(f"{number} 0 obj\\n".encode()); pdf.extend(obj.encode("latin-1", "replace")); pdf.extend(b"\\nendobj\\n")
    xref = len(pdf); pdf.extend(f"xref\\n0 {len(objects) + 1}\\n0000000000 65535 f \\n".encode())
    for offset in offsets[1:]: pdf.extend(f"{offset:010d} 00000 n \\n".encode())
    pdf.extend(f"trailer\\n<< /Size {len(objects) + 1} /Root 1 0 R >>\\nstartxref\\n{xref}\\n%%EOF\\n".encode())
    target.write_bytes(pdf)
    return target
