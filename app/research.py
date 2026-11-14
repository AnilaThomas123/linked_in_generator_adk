"""Evidence-first source collection for the editorial pipeline."""

from __future__ import annotations

import hashlib
import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import httpx
import yaml

from .config import ROOT
from .models import CandidateArticle, ResearchBrief


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self._in_title = False
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1
        self._in_title = tag == "title"

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        if tag in {"script", "style", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        clean = " ".join(data.split())
        if not clean or self._ignored_depth:
            return
        if self._in_title:
            self.title += f" {clean}"
        else:
            self.parts.append(clean)


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.current_url = ""
        self.current_text: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self.current_url = dict(attrs).get("href") or ""
            self.current_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self.current_url:
            self.links.append((self.current_url, " ".join(self.current_text).strip()))
            self.current_url = ""

    def handle_data(self, data: str) -> None:
        if self.current_url:
            self.current_text.append(" ".join(data.split()))


def _plain_text(body: str) -> tuple[str, str]:
    parser = _TextParser()
    parser.feed(body)
    return " ".join(parser.title.split()), " ".join(parser.parts)


def _response_text(response: httpx.Response) -> str:
    """Decode source bytes deterministically before evidence extraction.

    Some advisory pages omit or misstate their charset.  Using httpx's guessed
    ``response.text`` in those cases can introduce U+FFFD replacement
    characters, making verbatim evidence impossible to verify later.
    """
    raw = response.content
    for encoding in ("utf-8", response.encoding or "", "latin-1"):
        if not encoding:
            continue
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _published_date(body: str) -> str | None:
    patterns = [
        r'<meta[^>]+(?:property|name)=["\'](?:article:published_time|date|pubdate|publish_date)["\'][^>]+content=["\']([^"\']+)',
        r'<time[^>]+datetime=["\']([^"\']+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, body, flags=re.IGNORECASE)
        if match:
            return match.group(1)[:80]
    return None


def _article_links(body: str, page_url: str) -> list[tuple[str, str]]:
    parser = _LinkParser()
    parser.feed(body)
    base = urlparse(page_url)
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for href, label in parser.links:
        absolute = urljoin(page_url, href).split("#", 1)[0]
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"} or parsed.netloc != base.netloc:
            continue
        if absolute == page_url or absolute in seen or len(parsed.path.strip("/").split("/")) < 2:
            continue
        if any(part in parsed.path.lower() for part in ("about", "contact", "privacy", "login", "search")):
            continue
        seen.add(absolute)
        result.append((absolute, label or parsed.path.rsplit("/", 1)[-1].replace("-", " ")))
    return result[:5]


def load_source_registry() -> list[dict]:
    data = yaml.safe_load((ROOT / "config/sources.yaml").read_text(encoding="utf-8"))
    return data.get("sources", [])


def validate_research_brief(brief: ResearchBrief, candidates: list[CandidateArticle]) -> list[str]:
    """Reject analyst claims that are not anchored to fetched evidence."""
    by_url = {candidate.url for candidate in candidates}
    errors: list[str] = []
    item_ids = {item.id for item in brief.items}
    for item in brief.items:
        if item.url not in by_url:
            errors.append(f"item {item.id} cites URL not fetched by collector: {item.url}")
        if not item.id:
            errors.append("evidence item has an empty id")
    for topic in brief.topics:
        if not topic.item_ids:
            errors.append(f"topic candidate '{topic.headline}' has no evidence item IDs")
        missing = set(topic.item_ids) - item_ids
        if missing:
            errors.append(f"topic candidate '{topic.headline}' references missing item IDs: {sorted(missing)}")
    if not brief.items and brief.topics:
        errors.append("topic candidates were returned without evidence items")
    return errors


def _excerpt(text: str, limit: int = 700) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit].rstrip() + ("…" if len(text) > limit else "")


def _feed_entries(body: str) -> list[tuple[str, str, str, str]]:
    """Read RSS/Atom entries without trusting feed text as factual evidence."""
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        return []
    entries: list[tuple[str, str, str, str]] = []
    for item in root.iter():
        tag = item.tag.rsplit("}", 1)[-1].lower()
        if tag not in {"item", "entry"}:
            continue
        values = {}
        for child in item:
            child_tag = child.tag.rsplit("}", 1)[-1].lower()
            if child_tag == "link":
                values[child_tag] = child.attrib.get("href") or (child.text or "")
            else:
                values[child_tag] = " ".join((child.text or "").split())
        if values.get("title") and values.get("link"):
            entries.append((values["title"], values["link"], values.get("summary", values.get("description", "")), values.get("pubdate", values.get("published", values.get("updated", "")))))
    return entries[:10]


def _candidate_from_page(source: dict, response: httpx.Response, source_id: str) -> CandidateArticle | None:
    body = _response_text(response)
    title, text = _plain_text(body)
    if len(text) < 250:
        return None
    title = title or source["name"]
    if _is_weak_page(str(response.url), title, text):
        return None
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    (ROOT / "sources").mkdir(parents=True, exist_ok=True)
    (ROOT / "sources" / f"{content_hash[:16]}.txt").write_text(
        f"URL: {response.url}\nTITLE: {title}\nEXCERPT: {_excerpt(text)}\n",
        encoding="utf-8",
    )
    return CandidateArticle(
        source_id=source_id, topic_id=source["topic"], title=title[:300], url=str(response.url),
        publisher=source["name"], text=text[:30000], authority_score=float(source.get("authority_weight", 0)),
        published_at=_published_date(body), source_type=_source_type(source.get("source_type", "")),
    )


def _is_weak_page(url: str, title: str, text: str) -> bool:
    """Identify indexes/directories that cannot support an executive article."""
    parsed = urlparse(url)
    path = parsed.path.lower().rstrip("/")
    title_text = title.lower()
    weak_path = (
        path.endswith(("/blog", "/tools", "/directory", "/search"))
        or "/list/" in path
        or "/search/" in path
    )
    weak_title = any(
        phrase in title_text
        for phrase in ("search results", "recent submissions", "tools", "directory", "index")
    )
    weak_content = any(
        phrase in text[:5000].lower()
        for phrase in ("displaying 1 -", "search results", "browse all", "filter by")
    )
    return weak_path or weak_title or weak_content


def _source_type(value: str) -> str:
    normalized = value.lower()
    if any(token in normalized for token in ("government", "regulator", "standards", "research", "official", "advisory")):
        return "PRIMARY"
    if "vendor" in normalized or "company" in normalized:
        return "VENDOR CLAIM"
    return "SECONDARY"


def collect_candidates(timeout: float = 15.0, topic_id: str | None = None) -> list[CandidateArticle]:
    """Fetch configured sources and return only evidence-backed candidates.

    Network errors are recorded in logs by the caller and produce no fabricated
    candidate. This is deliberate: an evaluator must be able to abstain.
    """
    candidates: list[CandidateArticle] = []
    diagnostics: list[dict] = []
    sources = [source for source in load_source_registry() if topic_id is None or source["topic"] == topic_id]
    for source in sources:
        try:
            response = httpx.get(
                source["url"],
                timeout=timeout,
                follow_redirects=True,
                headers={"User-Agent": "linked-in-generator/0.1 (+local research tool)"},
            )
            response.raise_for_status()
            diagnostics.append({"source": source["name"], "status": "FETCHED"})
            response_body = _response_text(response)
            feed_entries = _feed_entries(response_body)
            if feed_entries:
                for entry_index, (entry_title, entry_url, entry_summary, entry_date) in enumerate(feed_entries):
                    opened_url = urljoin(str(response.url), entry_url)
                    try:
                        article = httpx.get(
                            opened_url, timeout=timeout, follow_redirects=True,
                            headers={"User-Agent": "linked-in-generator/0.1 (+local research tool)"},
                        )
                        article.raise_for_status()
                        candidate = _candidate_from_page(
                            source, article,
                            f"{source['name'].lower().replace(' ', '_')}_{entry_index}",
                        )
                        if candidate:
                            candidate.title = entry_title[:300] or candidate.title
                            candidate.published_at = candidate.published_at or entry_date or None
                            candidates.append(candidate)
                        else:
                            diagnostics.append({"source": source["name"], "url": opened_url, "status": "WEAK_OR_INSUFFICIENT_ARTICLE"})
                    except (httpx.HTTPError, OSError) as exc:
                        diagnostics.append({
                            "source": source["name"], "url": opened_url, "status": "ARTICLE_ERROR",
                            "error": f"{type(exc).__name__}: {str(exc)[:240]}",
                        })
                continue
            links = _article_links(response_body, str(response.url))
            if links:
                for link_index, (article_url, _) in enumerate(links):
                    article = httpx.get(
                        article_url, timeout=timeout, follow_redirects=True,
                        headers={"User-Agent": "linked-in-generator/0.1 (+local research tool)"},
                    )
                    article.raise_for_status()
                    candidate = _candidate_from_page(
                        source, article, f"{source['name'].lower().replace(' ', '_')}_{link_index}"
                    )
                    if candidate:
                        candidates.append(candidate)
                    else:
                        diagnostics.append({"source": source["name"], "url": article_url, "status": "WEAK_OR_INSUFFICIENT_ARTICLE"})
                continue
            candidate = _candidate_from_page(source, response, source["name"].lower().replace(" ", "_"))
            if candidate:
                candidates.append(candidate)
            else:
                diagnostics.append({"source": source["name"], "url": str(response.url), "status": "WEAK_OR_INSUFFICIENT_ARTICLE"})
        except (httpx.HTTPError, OSError, KeyError, ValueError) as exc:
            diagnostics.append({
                "source": source.get("name", "unknown"),
                "status": "ERROR",
                "error": f"{type(exc).__name__}: {str(exc)[:240]}",
            })
            continue
    diagnostic_path = ROOT / "data" / "adk_workflow" / f"source_diagnostics_{topic_id or 'all'}.json"
    diagnostic_path.parent.mkdir(parents=True, exist_ok=True)
    diagnostic_path.write_text(json.dumps(diagnostics, indent=2), encoding="utf-8")
    return candidates
