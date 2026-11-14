"""Evidence-grounded PNG infographic for approved LinkedIn posts (1080 x 1350, portrait).

Style A (bold editorial): deep navy background, white headline, one amber accent.
Text is measured and shrunk to fit each block. Long links are shortened, never clipped.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageFont

from .config import ROOT
from .models import Draft, EvaluationResult

WIDTH, HEIGHT = 1080, 1350
MARGIN = 80
CONTENT_WIDTH = WIDTH - 2 * MARGIN

# Style A palette
BG = (11, 29, 51)          # deep navy
TEXT = (255, 255, 255)     # headline and body
ACCENT = (255, 196, 61)    # amber, used once per section
SOFT = (176, 196, 222)     # secondary text
RULE = (38, 66, 100)       # dividers

# Vertical budget (px). Worst case must still fit on the card.
TOPIC_Y = 100
HEADLINE_TOP = 190
HEADLINE_MAX_H = 360
FOOTER_TOP = HEIGHT - 310
FIGURE_MAX_BOTTOM = FOOTER_TOP - 40

_FONT_FILES = {
    "bold": ["arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf"],
    "regular": ["arial.ttf", "Arial.ttf", "DejaVuSans.ttf"],
}


def _font(style: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in _FONT_FILES[style]:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    # Pillow >= 10.1 can scale its built-in font; the pin in pyproject.toml guarantees it.
    return ImageFont.load_default(size=size)


def _split_long_word(word: str, font, max_width: int) -> list[str]:
    """Break one word that is wider than the column into pieces that fit."""
    pieces: list[str] = []
    current = ""
    for char in word:
        trial = current + char
        if font.getlength(trial) <= max_width or not current:
            current = trial
        else:
            pieces.append(current)
            current = char
    if current:
        pieces.append(current)
    return pieces


def _wrap(text: str, font, max_width: int) -> list[str]:
    """Greedy word wrap by measured pixel width. Words too wide for a line are broken, never clipped."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        if font.getlength(word) > max_width:
            if current:
                lines.append(current)
                current = ""
            pieces = _split_long_word(word, font, max_width)
            lines.extend(pieces[:-1])
            current = pieces[-1]
            continue
        trial = f"{current} {word}".strip()
        if font.getlength(trial) <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def _fit(text: str, sizes: list[int], weight: str, width: int, max_height: int, ratio: float):
    """Try each font size from largest to smallest. Returns (font, line_gap, lines, fits)."""
    for size in sizes:
        font = _font(weight, size)
        gap = round(size * ratio)
        lines = _wrap(text, font, width)
        if len(lines) * gap <= max_height:
            return font, gap, lines, True
    # Nothing fits: return the smallest size so the caller can report the overflow.
    return font, gap, lines, False


def _display_url(url: str, font, width: int) -> tuple[list[str], bool]:
    """Show the link without scheme or tracking parameters. Shorten it if it is still too wide."""
    base = re.sub(r"^https?://", "", url.split("?", 1)[0].split("#", 1)[0]).rstrip("/")
    if font.getlength(base) > width:
        host, _, path = base.partition("/")
        last = path.rsplit("/", 1)[-1]
        base = f"{host}/…/{last}" if last else host
    lines = _wrap(base, font, width)
    return lines, len(lines) <= 2


def _first_number_sentence(content: str) -> tuple[str, str] | None:
    """The first figure in the post that has at least two digits or a percent sign, and its sentence."""
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", content):
        match = re.search(r"\d[\d,.]*%|\d[\d,.]*\d", sentence)
        if match:
            return match.group(0).rstrip(".,"), sentence.strip()
    return None


def render_infographic(draft: Draft) -> tuple[Path, list[str], bool]:
    """Render the PNG from the approved post only. Returns (path, drawn text, overflowed)."""
    if draft.decision != "APPROVED":
        raise ValueError("Infographics can only be generated after explicit approval")
    if not draft.sources:
        raise ValueError("Approved draft is missing source evidence")

    hook = next((line.strip() for line in draft.content.splitlines() if line.strip()), "")
    if not hook:
        raise ValueError("Approved draft has no opening line to use as the headline")
    source = draft.sources[0]
    topic = draft.topic_name.upper()

    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    canvas = ImageDraw.Draw(image)
    drawn: list[str] = []
    overflowed = False

    def draw(lines: list[str], font, fill, x: int, y: int, gap: int) -> int:
        for line in lines:
            canvas.text((x, y), line, font=font, fill=fill)
            drawn.append(line)
            y += gap
        return y

    # Topic label with accent bar
    canvas.rectangle((MARGIN, TOPIC_Y - 20, MARGIN + 60, TOPIC_Y - 12), fill=ACCENT)
    topic_font = _font("bold", 28)
    topic_lines = _wrap(topic, topic_font, CONTENT_WIDTH)
    overflowed |= len(topic_lines) > 1
    draw(topic_lines[:1], topic_font, ACCENT, MARGIN, TOPIC_Y, 36)

    # Figure: the first number in the approved post and its sentence
    figure = _first_number_sentence(draft.content)

    # Headline: the opening line of the approved post, shrunk until it fits.
    # Without a figure there is no panel below it, so the headline gets the room and a larger size.
    if figure:
        head_sizes, head_max_h = [68, 60, 54, 48, 42], HEADLINE_MAX_H
    else:
        head_sizes, head_max_h = [104, 92, 80, 68, 60, 54], 760
    head_font, head_gap, head_lines, ok = _fit(
        hook, head_sizes, "bold", CONTENT_WIDTH, head_max_h, 1.18)
    overflowed |= not ok
    y = draw(head_lines, head_font, TEXT, MARGIN, HEADLINE_TOP, head_gap)

    # Figure panel
    if figure:
        number, sentence = figure
        pad = 40
        inner = CONTENT_WIDTH - 2 * pad
        num_font, num_gap, num_lines, ok_n = _fit(number, [150, 124, 100], "bold", inner, 150, 1.0)
        # Skip the caption when it only repeats the headline; the figure alone carries the point.
        if _squash(sentence) != _squash(hook):
            cap_font, cap_gap, cap_lines, ok_c = _fit(sentence, [34, 30, 26], "regular", inner, 3 * 44, 1.3)
        else:
            cap_font, cap_gap, cap_lines, ok_c = None, 0, [], True
        overflowed |= not (ok_n and ok_c)

        box_top = y + 40
        caption_h = (16 + cap_gap * len(cap_lines)) if cap_lines else 0
        box_h = pad + num_gap * len(num_lines) + caption_h + pad
        canvas.rounded_rectangle(
            (MARGIN, box_top, WIDTH - MARGIN, box_top + box_h), radius=24, fill=ACCENT)
        inner_y = box_top + pad
        inner_y = draw(num_lines, num_font, BG, MARGIN + pad, inner_y, num_gap) + 16
        if cap_lines:
            draw(cap_lines, cap_font, BG, MARGIN + pad, inner_y, cap_gap)
        y = box_top + box_h
        overflowed |= y > FIGURE_MAX_BOTTOM

    # Footer: source only. Nothing outside the approved post and its source metadata.
    rule_y = FOOTER_TOP - 24
    canvas.line((MARGIN, rule_y, WIDTH - MARGIN, rule_y), fill=RULE, width=2)
    y = draw(["SOURCE"], _font("bold", 24), ACCENT, MARGIN, FOOTER_TOP, 34)

    title_font, title_gap, title_lines, ok_t = _fit(
        f"{source.title} ({source.publisher})", [32, 30, 28], "regular", CONTENT_WIDTH, 80, 1.25)
    overflowed |= not ok_t
    y = draw(title_lines, title_font, TEXT, MARGIN, y + 6, title_gap)

    url_font = _font("regular", 26)
    url_lines, ok_u = _display_url(source.url, url_font, CONTENT_WIDTH)
    overflowed |= not ok_u
    y = draw(url_lines, url_font, SOFT, MARGIN, y + 12, 34)

    disclaimer_font = _font("regular", 22)
    y = draw(["Based only on the cited source. No new claims added."], disclaimer_font, SOFT,
             MARGIN, y + 14, 30)
    overflowed |= y > HEIGHT - 40

    target = ROOT / "infographics" / f"{draft.draft_id}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, format="PNG", optimize=True)
    return target, drawn, overflowed


def _squash(text: str) -> str:
    """Remove all whitespace so wrapped lines compare equal to the original text."""
    return "".join(text.split())


def qa_infographic(draft: Draft, path: Path, drawn: list[str], overflowed: bool) -> EvaluationResult:
    reasons: list[str] = []
    if path.suffix.lower() != ".png" or not path.is_file():
        reasons.append("Artifact is not a PNG file.")
    else:
        with Image.open(path) as image:
            if image.size != (WIDTH, HEIGHT):
                reasons.append(f"Image size is {image.size}, expected {(WIDTH, HEIGHT)}.")
    if overflowed:
        reasons.append("Text overflowed its layout area.")
    joined = _squash(" ".join(drawn))
    host = urlparse(draft.sources[0].url).hostname if draft.sources else None
    if not host or host not in joined:
        reasons.append("The primary source's site is missing from the infographic.")
    hook = next((line.strip() for line in draft.content.splitlines() if line.strip()), "")
    if hook and _squash(hook) not in joined:
        reasons.append("The headline does not match the approved post's opening line.")
    figure = _first_number_sentence(draft.content)
    if figure and figure[0] not in joined:
        reasons.append("The highlighted figure does not match the approved post.")
    status = "PASS" if not reasons else "FAIL"
    return EvaluationResult(
        gate="VISUAL", status=status, score=5.0 if status == "PASS" else 0.0,
        reasons=reasons, evidence_checked=[source.url for source in draft.sources],
    )


def create_infographic(draft: Draft) -> Draft:
    """Create and QA a PNG infographic after approval; never invents data."""
    if draft.decision != "APPROVED":
        raise ValueError("Infographic generation requires an approved draft")
    draft.infographic_status = "GENERATING"
    try:
        path, drawn, overflowed = render_infographic(draft)
        result = qa_infographic(draft, path, drawn, overflowed)
        draft.infographic_qa = result
        if result.status != "PASS":
            draft.infographic_status = "FAILED"
            draft.infographic_error = "; ".join(result.reasons)
        else:
            draft.infographic_status = "READY"
            draft.infographic_path = str(path.relative_to(ROOT))
            draft.infographic_error = None
    except (OSError, ValueError) as exc:
        draft.infographic_status = "FAILED"
        draft.infographic_error = str(exc)
        draft.infographic_qa = EvaluationResult(gate="VISUAL", status="FAIL", score=0, reasons=[str(exc)])
    return draft
