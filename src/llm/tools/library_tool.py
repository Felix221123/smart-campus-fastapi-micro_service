# src/llm/tools/library_tool.py
from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import or_
from sqlalchemy.orm import Session

from src.llm.cache import cache, make_key
from src.module.models import LibraryResource


_LIMIT_RE = re.compile(r"\b(?:show|list|get|find|recommend)\s+(\d{1,2})\b", re.I)
_QUOTED_TITLE_RE = re.compile(r'["“](.+?)["”]')
_BY_AUTHOR_RE = re.compile(r"\bby\s+([a-z0-9][a-z0-9\s\.\-']{1,60})", re.I)

_WANT_AVAILABLE_RE = re.compile(r"\b(available|in stock|can i borrow|borrow|lend|loan)\b", re.I)
_WANT_LOCATION_RE = re.compile(r"\b(where is|located|location|which shelf|where can i find)\b", re.I)
_SERIES_RE = re.compile(r"\b(first|second|third|fourth|fifth|\d+(?:st|nd|rd|th)?)\s+book\s+in\b", re.I)

_RESOURCE_TYPE_HINTS: List[Tuple[re.Pattern[str], str]] = [
    (re.compile(r"\baudio\s*books?\b|\baudiobooks?\b", re.I), "audiobook"),
    (re.compile(r"\be-?books?\b", re.I), "ebook"),
    (re.compile(r"\bjournals?\b", re.I), "journal"),
    (re.compile(r"\bbooks?\b|\bnovels?\b|\bmemoirs?\b|\bautobiograph(?:y|ies)\b|\bbiograph(?:y|ies)\b", re.I), "book"),
]

_SUBJECT_PATTERNS: List[re.Pattern[str]] = [
    re.compile(r"\bbooks?\s+about\s+(.+)$", re.I),
    re.compile(r"\bbooks?\s+on\s+(.+)$", re.I),
    re.compile(r"\blooking\s+for\s+.+?\s+on\s+(.+)$", re.I),
    re.compile(r"\blooking\s+for\s+.+?\s+about\s+(.+)$", re.I),
    re.compile(r"\brecommend\s+(?:me\s+)?(?:a|some|any)?\s*(.+?)\s+(?:books?|novels?|journals?|audiobooks?)\b", re.I),
    re.compile(r"\bwhere\s+are\s+your\s+books?\s+about\s+(.+)$", re.I),
    re.compile(r"\bi\s+need\s+some\s+(.+?)\s+(?:audiobooks?|books?|journals?)\b", re.I),
]

_LEAD_IN_PATTERNS: List[re.Pattern[str]] = [
    re.compile(r"^(?:can you\s+)?find\s+(?:me\s+)?(?:the\s+)?(?:book|ebook|e-?book|audiobook|journal|novel|memoir|autobiography|biography)\s+", re.I),
    re.compile(r"^(?:can you\s+)?find\s+(?:me\s+)?", re.I),
    re.compile(r"^do you have\s+", re.I),
    re.compile(r"^i need\s+", re.I),
    re.compile(r"^where is\s+(?:the\s+)?", re.I),
    re.compile(r"^is there\s+", re.I),
    re.compile(r"^locate\s+", re.I),
]

_TRAILING_NOISE_PATTERNS: List[re.Pattern[str]] = [
    re.compile(r"\s+for me\??$", re.I),
    re.compile(r"\s+available(?:\s+right\s+now)?\??$", re.I),
    re.compile(r"\s+right\s+now\??$", re.I),
    re.compile(r"\s+located\??$", re.I),
    re.compile(r"\s+in the library\??$", re.I),
    re.compile(r"\?$"),
]

_STOPWORDS = {
    "a", "an", "the", "me", "for", "to", "of", "on", "about", "some", "any",
    "good", "best", "please", "can", "you", "find", "need", "looking", "look",
    "recommend", "where", "is", "are", "your", "our", "have", "do", "i",
    "right", "now", "available", "located", "books", "book", "journal",
    "journals", "novel", "novels", "ebook", "ebooks", "audiobook", "audiobooks"
}


def _norm(text: str) -> str:
    return " ".join((text or "").lower().strip().split())


def _parse_limit(question: str, default: int = 5) -> int:
    m = _LIMIT_RE.search(question or "")
    if not m:
        return default
    return max(1, min(int(m.group(1)), 10))


def _resource_type_hint(question: str) -> Optional[str]:
    for pattern, value in _RESOURCE_TYPE_HINTS:
        if pattern.search(question or ""):
            return value
    return None


def _extract_author(question: str) -> Optional[str]:
    m = _BY_AUTHOR_RE.search(question or "")
    if not m:
        return None
    author = m.group(1).strip(" .?")
    # trim trailing availability words
    author = re.sub(r"\b(available|right now|located|for me)\b.*$", "", author, flags=re.I).strip(" .?")
    return author or None


def _strip_noise(text: str) -> str:
    out = (text or "").strip()
    for pattern in _LEAD_IN_PATTERNS:
        out = pattern.sub("", out).strip()
    for pattern in _TRAILING_NOISE_PATTERNS:
        out = pattern.sub("", out).strip()
    return out.strip(" .")


def _extract_direct_title(question: str) -> Optional[str]:
    quoted = _QUOTED_TITLE_RE.search(question or "")
    if quoted:
        return quoted.group(1).strip()

    raw = _strip_noise(question or "")
    raw = re.sub(r"\bby\s+[a-z0-9][a-z0-9\s\.\-']{1,60}$", "", raw, flags=re.I).strip()

    # avoid treating thematic requests as exact titles
    if re.search(r"\babout\b|\bon\b|\brecommend\b|\blooking for\b", raw, re.I):
        return None

    if len(raw) < 2:
        return None

    generic = {
        "a book", "some books", "a journal", "audiobooks", "books", "journals",
        "book", "journal", "novel", "memoir", "autobiography", "biography"
    }
    if _norm(raw) in generic:
        return None

    return raw


def _extract_subject(question: str) -> Optional[str]:
    for pattern in _SUBJECT_PATTERNS:
        m = pattern.search(question or "")
        if m:
            subject = m.group(1).strip(" .?")
            return subject or None
    return None


def _tokenize(text: str) -> List[str]:
    tokens = re.findall(r"[a-z0-9']+", (text or "").lower())
    return [t for t in tokens if len(t) >= 3 and t not in _STOPWORDS]


def _library_key(question: str, limit: int) -> str:
    norm_q = _norm(question)
    q_hash = hashlib.sha256(norm_q.encode("utf-8")).hexdigest()[:16]
    return make_key("library", "v1", limit, q_hash)


def _availability_text(is_available: bool) -> str:
    return "available now" if is_available else "currently unavailable"


def _resource_option(row: LibraryResource) -> Dict[str, Any]:
    location = row.location or "location not listed"
    return {
        "resource_id": str(row.id),
        "title": row.title,
        "author": row.author,
        "category": row.category,
        "resource_type": row.resource_type,
        "location": location,
        "is_available": bool(row.is_available),
        "snippet": (
            f"{row.resource_type.title()} by {row.author}. "
            f"{_availability_text(bool(row.is_available))}. "
            f"Location: {location}."
        ),
    }


def _score_row(
    row: LibraryResource,
    *,
    direct_title: Optional[str],
    subject: Optional[str],
    author: Optional[str],
    type_hint: Optional[str],
    tokens: List[str],
    wants_available: bool,
) -> int:
    score = 0

    title_n = _norm(row.title)
    author_n = _norm(row.author)
    category_n = _norm(row.category)
    type_n = _norm(row.resource_type)

    direct_n = _norm(direct_title or "")
    subject_n = _norm(subject or "")
    author_q = _norm(author or "")

    if direct_n:
        if title_n == direct_n:
            score += 140
        elif direct_n in title_n:
            score += 100

    if author_q:
        if author_n == author_q:
            score += 80
        elif author_q in author_n:
            score += 55

    if subject_n:
        if category_n == subject_n:
            score += 70
        elif subject_n in category_n:
            score += 50
        elif subject_n in title_n:
            score += 35

    if type_hint and type_hint in type_n:
        score += 20

    for token in tokens:
        if token in title_n:
            score += 12
        if token in author_n:
            score += 8
        if token in category_n:
            score += 10
        if token in type_n:
            score += 6

    if wants_available and row.is_available:
        score += 15

    return score


def run(db: Session, question: str, limit: int = 5) -> Dict[str, Any]:
    limit = _parse_limit(question, limit)
    ck = _library_key(question, limit)
    cached = cache().get(ck)
    if cached is not None:
        return cached

    q = question or ""
    q_lower = q.lower()

    type_hint = _resource_type_hint(q)
    wants_available = bool(_WANT_AVAILABLE_RE.search(q))
    wants_location = bool(_WANT_LOCATION_RE.search(q))
    direct_title = _extract_direct_title(q)
    author = _extract_author(q)
    subject = _extract_subject(q)
    tokens = _tokenize(subject or direct_title or q)

    if _SERIES_RE.search(q):
        out = {
            "error": "series_metadata_missing",
            "summary": (
                "I can search the catalogue, but I can’t reliably identify book order in a series "
                "from the current library data. Try the exact title or author."
            ),
        }
        cache().set(ck, out, ttl_seconds=60)
        return out

    search_mode = "thematic" if subject and not direct_title else "direct"

    qry = db.query(LibraryResource)

    if type_hint:
        qry = qry.filter(LibraryResource.resource_type.ilike(f"%{type_hint}%"))

    if author:
        qry = qry.filter(LibraryResource.author.ilike(f"%{author}%"))

    phrase = subject or direct_title

    if phrase:
        like = f"%{phrase}%"
        qry = qry.filter(
            or_(
                LibraryResource.title.ilike(like),
                LibraryResource.author.ilike(like),
                LibraryResource.category.ilike(like),
                LibraryResource.resource_type.ilike(like),
            )
        )
    elif tokens:
        ors = []
        for token in tokens[:6]:
            like = f"%{token}%"
            ors.extend(
                [
                    LibraryResource.title.ilike(like),
                    LibraryResource.author.ilike(like),
                    LibraryResource.category.ilike(like),
                    LibraryResource.resource_type.ilike(like),
                ]
            )
        qry = qry.filter(or_(*ors))

    rows = qry.limit(40).all()

    # fallback: very broad type-only lookup
    if not rows and type_hint:
        rows = (
            db.query(LibraryResource)
            .filter(LibraryResource.resource_type.ilike(f"%{type_hint}%"))
            .limit(20)
            .all()
        )

    if not rows:
        out = {
            "count": 0,
            "query_mode": search_mode,
            "summary": "I couldn’t find a matching library resource in the catalogue.",
            "error": "no_library_match",
        }
        cache().set(ck, out, ttl_seconds=60)
        return out

    ranked = sorted(
        rows,
        key=lambda row: _score_row(
            row,
            direct_title=direct_title,
            subject=subject,
            author=author,
            type_hint=type_hint,
            tokens=tokens,
            wants_available=wants_available,
        ),
        reverse=True,
    )

    top = ranked[:limit]
    options = [_resource_option(r) for r in top]

    # strong single-hit path
    best = top[0]
    best_score = _score_row(
        best,
        direct_title=direct_title,
        subject=subject,
        author=author,
        type_hint=type_hint,
        tokens=tokens,
        wants_available=wants_available,
    )
    second_score = (
        _score_row(
            top[1],
            direct_title=direct_title,
            subject=subject,
            author=author,
            type_hint=type_hint,
            tokens=tokens,
            wants_available=wants_available,
        )
        if len(top) > 1
        else -999
    )

    strong_single = (len(top) == 1) or (direct_title and best_score >= 100 and (best_score - second_score) >= 25)

    if strong_single:
        chosen = _resource_option(best)
        out = {
            "count": 1,
            "query_mode": search_mode,
            "chosen": chosen,
            "summary": (
                f"{chosen['title']} by {chosen['author']} is {_availability_text(chosen['is_available'])}. "
                f"It’s in {chosen['location']}."
                if wants_location or wants_available or search_mode == "direct"
                else f"I found {chosen['title']} by {chosen['author']} in the library catalogue."
            ),
        }
        cache().set(ck, out, ttl_seconds=120)
        return out

    out = {
        "count": len(options),
        "query_mode": search_mode,
        "options": options,
        "requires_user_choice": True,
        "summary": (
            f"I found {len(options)} library resources that look relevant."
            if search_mode == "thematic"
            else f"I found {len(options)} possible matches in the library catalogue."
        ),
    }
    cache().set(ck, out, ttl_seconds=120)
    return out
