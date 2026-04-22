# src/module/assistant_service.py
from __future__ import annotations
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from src.utils.cache import get_cache, make_cache_key
from src.utils.openai_client import chat_complete
from src.module.knowledge_service import search_knowledge
from src.module.models import TimetableEntry


_TIMETABLE_HINTS = {"class", "classes", "timetable", "lecture", "lab", "tutorial", "tomorrow", "today"}


def detect_intent(question: str) -> str:
    q = (question or "").lower()
    if any(w in q for w in _TIMETABLE_HINTS):
        return "timetable"
    return "knowledge"


def _tomorrow_weekday_name() -> str:
    tomorrow = datetime.now().date() + timedelta(days=1)
    return tomorrow.strftime("%A")  # e.g. "Monday"


def query_timetable_for_day(db: Session, day_name: str) -> List[Dict[str, Any]]:
    rows = (
        db.query(TimetableEntry)
        .filter(TimetableEntry.day_of_week.ilike(day_name))
        .order_by(TimetableEntry.start_time.asc())
        .all()
    )
    return [
        {
            "room": r.room,
            "start_time": r.start_time.isoformat(),
            "end_time": r.end_time.isoformat(),
            "session_type": r.session_type,
            "day_of_week": r.day_of_week,
            "module_id": str(r.module_id),
        }
        for r in rows
    ]


def answer_with_timetable(db: Session, question: str) -> Dict[str, Any]:
    cache = get_cache()
    day = _tomorrow_weekday_name() if "tomorrow" in question.lower() else datetime.utcnow().strftime("%A")

    ck = make_cache_key("timetable", day)
    cached = cache.get(ck)
    if cached is None:
        sessions = query_timetable_for_day(db, day)
        cache.set(ck, sessions, ttl_seconds=30)
    else:
        sessions = cached

    system = (
        "You are a smart campus assistant. "
        "Use the provided timetable sessions to answer clearly. "
        "If there are no sessions, say so."
    )
    user = f"""User question: {question}

Timetable sessions for {day} (JSON):
{sessions}

Reply with a friendly concise answer. Include times and rooms if available.
"""

    answer = chat_complete(system=system, user=user, temperature=0.2, max_tokens=300)
    return {"answer": answer, "intent": "timetable", "day": day, "sessions": sessions}


def answer_with_rag(db: Session, question: str) -> Dict[str, Any]:
    cache = get_cache()
    ck = make_cache_key("rag_answer", question)
    cached = cache.get(ck)
    if cached is not None:
        return cached

    hits = search_knowledge(db, query=question, top_k=5)

    context_blocks = []
    for h in hits:
        context_blocks.append(
            f"- Source: {h['document_title']} | uri: {h['uri']} | similarity: {h['similarity']:.3f}\n"
            f"  Text: {h['text']}"
        )
    context = "\n\n".join(context_blocks) if context_blocks else "No relevant context found."

    system = (
        "You are a smart campus assistant. "
        "Answer using ONLY the provided context when possible. "
        "If context is insufficient, say what is missing and give a best-effort general answer."
    )
    user = f"""User question: {question}

Context:
{context}

Return:
1) A direct helpful answer
2) A short Sources list (document title + uri) if sources exist.
"""

    answer = chat_complete(system=system, user=user, temperature=0.2, max_tokens=450)
    out = {"answer": answer, "intent": "knowledge", "sources": hits}
    cache.set(ck, out, ttl_seconds=60)
    return out


def answer_question(db: Session, question: str) -> Dict[str, Any]:
    intent = detect_intent(question)
    if intent == "timetable":
        return answer_with_timetable(db, question)
    return answer_with_rag(db, question)
