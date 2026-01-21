# src/llm/tools/spaces_tool.py
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy import desc

from src.module.models import Space  


_ACCESSIBLE_PATTERNS = [r"\baccessible\b", r"\bwheelchair\b", r"\bstep[-\s]?free\b"]
_QUIET_PATTERNS = [r"\bquiet\b", r"\bsilent\b", r"\bcalm\b", r"\bpeaceful\b", r"\bnois(e|y)\b"]
_LIBRARY_PATTERNS = [r"\blibrary\b", r"\blearning\s+resource\b", r"\bboot(s)?\s+library\b", r"\bnls\b"]

_GROUP_PATTERNS = [r"\bgroup\b", r"\bteam\b", r"\bcollab(orate|oration)?\b", r"\bmeeting\b"]
_INDIVIDUAL_PATTERNS = [r"\balone\b", r"\bsolo\b", r"\bindividual\b", r"\bby\s+myself\b"]

_CAPACITY_RE = re.compile(r"\b(?:for|fits?|capacity)\s*(\d{1,2})\s*(?:people|persons|students)?\b", re.I)

_TYPE_HINTS = [
    (re.compile(r"\bpod\b", re.I), "pod"),
    (re.compile(r"\b(room|study\s*room|group\s*room)\b", re.I), "room"),
    (re.compile(r"\bdesk\b", re.I), "desk"),
    (re.compile(r"\bcomputer|pc\b", re.I), "computer"),
    (re.compile(r"\bsilent\s*zone\b", re.I), "silent"),
]

_LOCATION_HINTS = [
    (re.compile(r"\bcity\s+campus\b", re.I), "city"),
    (re.compile(r"\bclifton\b", re.I), "clifton"),
    (re.compile(r"\bbrackenhurst\b", re.I), "brackenhurst"),
    (re.compile(r"\blibrary\b", re.I), "library"),
]


def _matches_any(q: str, patterns: List[str]) -> bool:
    return any(re.search(p, q) for p in patterns)

def _extract_capacity(q: str) -> Optional[int]:
    m = _CAPACITY_RE.search(q or "")
    if not m:
        return None
    n = int(m.group(1))
    return max(1, min(n, 50))

def _extract_type_hint(q: str) -> Optional[str]:
    for rgx, hint in _TYPE_HINTS:
        if rgx.search(q or ""):
            return hint
    return None


def _extract_location_hint(q: str) -> Optional[str]:
    for rgx, hint in _LOCATION_HINTS:
        if rgx.search(q or ""):
            return hint
    return None

# running the operation in the DB
def run(db: Session, question: str, limit: int = 10) -> Dict[str, Any]:
    q = (question or "").lower()

    want_accessible = _matches_any(q, _ACCESSIBLE_PATTERNS)
    want_quiet = _matches_any(q, _QUIET_PATTERNS)
    want_library = _matches_any(q, _LIBRARY_PATTERNS)
    want_group = _matches_any(q, _GROUP_PATTERNS)

    capacity_min = _extract_capacity(q)
    type_hint = _extract_type_hint(q)
    location_hint = _extract_location_hint(q)

    qry = db.query(Space)

    if want_accessible:
        qry = qry.filter(Space.is_accessible.is_(True))

    if want_library:
        qry = qry.filter(Space.location.ilike("%library%"))

    if location_hint and not want_library:
        qry = qry.filter(Space.location.ilike(f"%{location_hint}%"))

    if capacity_min is not None:
        qry = qry.filter(Space.capacity >= capacity_min)

    if type_hint:
        qry = qry.filter(Space.type.ilike(f"%{type_hint}%"))

    if want_quiet:
        qry = qry.order_by(desc(Space.quiet_score.nullslast()))
    else:
        qry = qry.order_by(Space.name.asc())

    rows = qry.limit(limit).all()

    spaces: List[Dict[str, Any]] = []
    for s in rows:
        spaces.append(
            {
                "space_id": str(s.id),
                "name": s.name,
                "type": s.type,
                "location": s.location,
                "quiet_score": float(s.quiet_score) if s.quiet_score is not None else None,
                "is_accessible": bool(s.is_accessible),
                "capacity": s.capacity,
            }
        )

    return {
        "filters": {
            "accessible": want_accessible,
            "quiet": want_quiet,
            "library": want_library,
            "group": want_group,
            "capacity_min": capacity_min,
            "type_hint": type_hint,
            "location_hint": location_hint,
        },
        "count": len(spaces),
        "spaces": spaces,
        "summary": f"Found {len(spaces)} space(s) matching your preferences.",
    }