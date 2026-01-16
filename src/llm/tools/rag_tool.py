# src/llm/tools/rag_tool.py
from __future__ import annotations
from typing import Any, Dict

from sqlalchemy.orm import Session
import hashlib
from src.module.knowledge_service import search_knowledge  

from sqlalchemy.orm import Session
from src.module.knowledge_service import search_knowledge
from src.llm.cache import cache, make_key


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

    hits = search_knowledge(db, query=question, top_k=top_k)
    out = {"hits": hits}
    c.set(k, out, ttl_seconds=900)  # 15 mins
    return out
