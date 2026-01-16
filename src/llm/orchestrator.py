# main orchestrator
# Main entrypoint:
# loads conversation history (DB)
# checks pending actions (eg booking flow)
# routes question → tool(s)
# calls tool(s)
# uses LLM to turn tool output into a friendly answer
# writes messages + events back to DB


# src/llm/orchestrator.py
from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, Optional

import openai
from sqlalchemy.orm import Session

from src.llm.router import route
from src.llm.prompts import ANSWER_SYSTEM
from src.llm.memory import (
    ensure_conversation,
    append_message,
    recent_messages,
    log_event,
    get_metadata,
    set_metadata,
)

from src.llm.tools import (
    timetable_tool,
    assessments_tool,
    events_tool,
    spaces_tool,
    rag_tool,
    notifications_tool,
    space_booking_tool,
)

client = openai.OpenAI()
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")


_ORDINAL = {
    "first": 1, "1st": 1,
    "second": 2, "2nd": 2,
    "third": 3, "3rd": 3,
    "fourth": 4, "4th": 4,
    "fifth": 5, "5th": 5,
}



def _build_rag_options(hits: list[dict], max_options: int = 5) -> list[dict]:
    opts = []
    for h in (hits or [])[:max_options]:
        opts.append({
            "title": h.get("document_title"),
            "uri": h.get("uri"),
            "snippet": h.get("text"),
            "chunk_id": h.get("chunk_id"),
            "document_id": h.get("document_id"),
            "similarity": h.get("similarity"),
        })
    return opts






def _extract_option_number(text: str) -> Optional[int]:
    t = (text or "").lower()

    # "option 2"
    m = re.search(r"\boption\s*(\d+)\b", t)
    if m:
        return int(m.group(1))

    # "the second one"
    for k, v in _ORDINAL.items():
        if re.search(rf"\b{k}\b", t):
            return v

    # plain "2"
    m2 = re.search(r"\b(\d+)\b", t)
    if m2:
        return int(m2.group(1))

    return None





# composing an answer after users query
def _compose_answer(question: str, tool: str, tool_output: Dict[str, Any], history: list[dict]) -> str:
    # Make it voice-friendly and concise
    messages = [{"role": "system", "content": ANSWER_SYSTEM}]
    # include a short history window for personalization
    for h in history[-8:]:
        messages.append({"role": h["role"], "content": h["content"]})

    messages.append(
        {
            "role": "user",
            "content": f"""
User question: {question}

Tool used: {tool}
Tool output (JSON):
{tool_output}

Write the best spoken-style answer. If tool_output includes options, list them as:
Option 1: ...
Option 2: ...
Then ask user to pick one.
""".strip(),
        }
    )

    resp = client.chat.completions.create(
        model=LLM_MODEL,
        messages=messages,
        temperature=0.2,
        max_tokens=350,
    )
    return resp.choices[0].message.content.strip()


def run(
    db: Session,
    question: str,
    user_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    channel: str = "web",
) -> Dict[str, Any]:
    # 1) ensure conversation exists
    cid = ensure_conversation(db, user_id=user_id, channel=channel, conversation_id=conversation_id)

    # 2) load metadata + history
    meta = get_metadata(db, cid)
    pending = meta.get("pending_action")

    # 3) store user message
    append_message(db, cid, role="user", content=question)

    # 4) route
    routing = route(question, pending_action=pending)
    tool = routing["tool"]
    log_event(db, cid, user_id, "intent_detected", {"tool": tool, "reason": routing.get("reason")})

    tool_output: Dict[str, Any] = {}
    t0 = time.time()

    # --- handle pending rag pick (user choosing option) ---
    if tool == "rag" and pending and pending.get("type") == "rag_pick":
        selection = _extract_option_number(question)
        options = pending.get("options", [])

        if selection is None or selection < 1 or selection > len(options):
            tool_output = {
                "error": "missing_selection",
                "message": "No worries — please say something like: 'option 1' or 'the second one'.",
                "options": options,
                "requires_user_choice": True,
            }
        else:
            chosen = options[selection - 1]
            tool_output = {
                "chosen_option": selection,
                "chosen": chosen,
                "message": "Got it — here’s the info for that option.",
            }

            # clear pending action once chosen
            meta["pending_action"] = None
            set_metadata(db, cid, meta)

    # 5) handle booking confirmation step
    if tool == "space_booking" and pending and pending.get("type") == "space_booking":
        selection = _extract_option_number(question)
        if selection is None:
            tool_output = {
                "error": "missing_selection",
                "message": "Please say something like 'book option 2'.",
                "options": pending.get("options", []),
            }
        else:
            tool_output = space_booking_tool.run_confirm(db, user_id=user_id, selection_index=selection, pending=pending)
            if tool_output.get("confirmed"):
                # clear pending action
                meta["pending_action"] = None
                set_metadata(db, cid, meta)

    # 6) normal tool execution
    if not tool_output:
        if tool == "timetable":
            tool_output = timetable_tool.run(db, question)
        elif tool == "assessments":
            tool_output = assessments_tool.run(db, question)
        elif tool == "events":
            tool_output = events_tool.run(db, question)
        elif tool == "spaces":
            tool_output = spaces_tool.run(db, question)
        elif tool == "notifications":
            if not user_id:
                tool_output = {"error": "missing_user", "message": "Please sign in so I can read your notifications."}
            else:
                tool_output = notifications_tool.run(db, user_id=user_id)
        elif tool == "space_booking":
            if not user_id:
                tool_output = {"error": "missing_user", "message": "Please sign in so I can book a space for you."}
            else:
                tool_output = space_booking_tool.run_find(db, question)
                # store pending choice if options exist
                if tool_output.get("requires_user_choice"):
                    meta["pending_action"] = {
                        "type": "space_booking",
                        "start_time": tool_output["start_time"],
                        "end_time": tool_output["end_time"],
                        "options": tool_output["options"],
                    }
                    set_metadata(db, cid, meta)
        else:
            tool_output = rag_tool.run(db, question)

            # if we got hits, create options and store pending pick
            hits = tool_output.get("hits") or []
            options = _build_rag_options(hits, max_options=5)

            if options:
                tool_output["options"] = options
                tool_output["requires_user_choice"] = True

                meta["pending_action"] = {
                    "type": "rag_pick",
                    "options": options,
                    "original_question": question,
                }
                set_metadata(db, cid, meta)

    latency_ms = int((time.time() - t0) * 1000)
    log_event(db, cid, user_id, "tool_result", {"tool": tool, "latency_ms": latency_ms})

    # 7) compose final response via LLM
    history = recent_messages(db, cid, limit=12)
    answer = _compose_answer(question, tool, tool_output, history)

    # 8) store assistant message
    append_message(
        db,
        cid,
        role="assistant",
        content=answer,
        tool_name=tool,
        tool_payload=tool_output,
        latency_ms=latency_ms,
    )

    return {
        "conversation_id": cid,
        "tool": tool,
        "answer": answer,
        "tool_output": tool_output,
    }
