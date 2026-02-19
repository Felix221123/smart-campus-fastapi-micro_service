# src/llm/tools/rag_tool.py
from __future__ import annotations
from typing import Any, Dict
import re

from sqlalchemy.orm import Session
import hashlib
from src.module.knowledge_service import search_knowledge

from src.llm.cache import cache, make_key


_SEED_PREFIX_RE = re.compile(r"^\s*seed\s*doc\s*:\s*", re.I)
_INTERNAL_NOTE_RE = re.compile(
    r"\b(voice-agent\s*tip|in-app\s*tip|best\s*practice|always\s+link|your\s+app\s+should|"
    r"your\s+agent\s+should|store\s+link\s+if\s+you\s+ingest|public\s+guidance)\b",
    re.I,
)


def _clean_title(title: str) -> str:
    t = (title or "").strip()
    t = _SEED_PREFIX_RE.sub("", t)
    return t


def _clean_text(text: str) -> str:
    t = " ".join((text or "").split())
    if not t:
        return ""

    parts = re.split(r"(?<=[.!?])\s+", t)
    kept = [p for p in parts if p and not _INTERNAL_NOTE_RE.search(p)]
    cleaned = " ".join(kept).strip()

    if cleaned:
        return cleaned

    # fallback when chunk text is mostly internal authoring guidance
    return "Please check the official page for full details."


def _sanitize_hits(hits: list[dict]) -> list[dict]:
    out: list[dict] = []
    for h in hits or []:
        row = dict(h)
        row["document_title"] = _clean_title(str(row.get("document_title") or ""))
        row["text"] = _clean_text(str(row.get("text") or ""))
        out.append(row)
    return out


def _rag_key(question: str, top_k: int) -> str:
    norm = " ".join((question or "").lower().split())
    h = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]
    return make_key("rag", "v1", top_k, h)


def run(db: Session, question: str, top_k: int = 5) -> Dict[str, Any]:
    c = cache()
    k = _rag_key(question, top_k)
    hit = c.get(k)
    if hit is not None:
        return hit

    hits = _sanitize_hits(search_knowledge(db, query=question, top_k=top_k))
    out = {"hits": hits}
    c.set(k, out, ttl_seconds=900)  # 15 mins
    return out
