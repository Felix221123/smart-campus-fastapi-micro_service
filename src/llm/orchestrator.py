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
import json

from src.llm.cache import cache, make_key
from src.module.models import User

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
    library_tool,
)
from src.analytics.tracking import log_query_analytics

client = openai.OpenAI()
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")


_ORDINAL = {
    "first": 1, "1st": 1,
    "second": 2, "2nd": 2,
    "third": 3, "3rd": 3,
    "fourth": 4, "4th": 4,
    "fifth": 5, "5th": 5,
}


# Repeat / cancel phrases (so you can escape or re-show options without getting stuck)
_REPEAT_OPTIONS_RE = re.compile(r"\b(options|show options|repeat|list them|what are the options|show me the options)\b", re.I)
_CANCEL_RE = re.compile(r"\b(cancel|stop|nevermind|never mind|forget it|abort|drop it)\b", re.I)

# Default TTLs (seconds)
_PENDING_TTL_DEFAULT = {
    "rag_pick": 300,
    "space_booking": 600,
    "library_pick": 300,
}


_YES_RE = re.compile(r"\b(yes|yeah|yep|sure|ok|okay|confirm|book it)\b", re.I)
_NO_RE = re.compile(r"\b(no|nah|nope|cancel)\b", re.I)
_QUESTION_WORD_RE = re.compile(r"\b(what|which|when|where|who|why|how|can\s+i|could\s+i|do\s+i)\b", re.I)
_LINK_REQUEST_RE = re.compile(r"\b(link|url|website|site|send\s+me\s+the\s+link|where\s+is\s+the\s+link)\b", re.I)
_MULTI_CHOICE_RE = re.compile(r"\b(options?|list|show\s+me|which\s+one|compare|choices?)\b", re.I)
_SEED_PREFIX_RE = re.compile(r"^\s*seed\s*doc\s*:\s*", re.I)
_INTERNAL_NOTE_RE = re.compile(
    r"\b(voice-agent\s*tip|in-app\s*tip|best\s*practice|always\s+link|your\s+app\s+should|"
    r"your\s+agent\s+should|store\s+link\s+if\s+you\s+ingest|public\s+guidance)\b",
    re.I,
)



def _build_rag_options(hits: list[dict], max_options: int = 5) -> list[dict]:
    def _shorten(text: str, limit: int = 140) -> str:
        clean = " ".join((text or "").split())
        if len(clean) <= limit:
            return clean
        return clean[: limit - 1].rstrip() + "…"

    opts = []
    seen: set[tuple[str, str]] = set()
    for h in (hits or []):
        key = (str(h.get("uri") or ""), str(h.get("document_title") or ""))
        if key in seen:
            continue
        seen.add(key)
        opts.append({
            "title": _shorten(h.get("document_title")),
            "uri": h.get("uri"),
            "snippet": _shorten(h.get("text")),
            "chunk_id": h.get("chunk_id"),
            "document_id": h.get("document_id"),
            "similarity": h.get("similarity"),
        })
        if len(opts) >= max_options:
            break
    return opts


def _is_link_request(text: str) -> bool:
    return bool(_LINK_REQUEST_RE.search(text or ""))


def _should_require_rag_choice(question: str, hits: list[dict]) -> bool:
    q = (question or "").strip().lower()
    if not hits:
        return False
    if _is_link_request(q):
        return False
    return bool(_MULTI_CHOICE_RE.search(q))


def _clean_user_title(title: str) -> str:
    return _SEED_PREFIX_RE.sub("", (title or "").strip())


def _clean_user_snippet(text: str) -> str:
    clean = " ".join((text or "").split())
    if not clean:
        return ""
    if _INTERNAL_NOTE_RE.search(clean):
        return ""
    return clean

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


def _is_repeat_or_cancel(text: str) -> bool:
    q = text or ""
    return bool(_REPEAT_OPTIONS_RE.search(q) or _CANCEL_RE.search(q))


def _looks_like_new_question(text: str) -> bool:
    q = (text or "").strip()
    if not q:
        return False
    if _is_repeat_or_cancel(q):
        return False
    if _extract_option_number(q) is not None:
        return False
    ql = q.lower()
    return "?" in ql or bool(_QUESTION_WORD_RE.search(ql))

# Ensure that if there’s a relevant link in the tool output, it gets included in the answer, especially if the user is asking for a link. But even if they aren’t explicitly asking for a link, we want to include it when relevant to avoid unnecessary follow-up turns.
def _best_link_from_tool_output(tool_output: Dict[str, Any]) -> Optional[str]:
    chosen = tool_output.get("chosen")
    if isinstance(chosen, dict) and chosen.get("uri"):
        return str(chosen["uri"])

    options = tool_output.get("options") or []
    for option in options:
        if isinstance(option, dict) and option.get("uri"):
            return str(option["uri"])

    hits = tool_output.get("hits") or []
    for hit in hits:
        if isinstance(hit, dict) and hit.get("uri"):
            return str(hit["uri"])

    return None

# Ensure that if there’s a relevant link in the tool output, it gets included in the answer, especially if the user is asking for a link. But even if they aren’t explicitly asking for a link, we want to include it when relevant to avoid unnecessary follow-up turns.
def _ensure_link_in_answer(answer: str, tool_output: Dict[str, Any], question: str) -> str:
    link = _best_link_from_tool_output(tool_output)
    if not link:
        return answer

    if link in (answer or ""):
        return answer

    q = (question or "").lower()
    asks_for_link = "link" in q or "url" in q or "website" in q
    if asks_for_link:
        return f"{(answer or '').rstrip()} Here’s the link: {link}".strip()

    # Always include at least one relevant link when available
    return f"{(answer or '').rstrip()} You can check it here: {link}".strip()


def _option_label(idx: int) -> str:
    if idx == 1:
        return "option 1"
    if idx == 2:
        return "option 2"
    return f"option {idx}"

# Pick the intro sentence from the answer to show above options, stripping out any existing option lists or internal notes. If no good intro is found, return a default intro.
def _pick_intro_from_answer(answer: str) -> str:
    text = (answer or "").strip()
    if not text:
        return "I found a few relevant options."

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    kept: list[str] = []
    for ln in lines:
        lower = ln.lower()
        if lower.startswith("here are your options"):
            continue
        if re.match(r"^option\s*\d+\s*:", ln, re.I):
            continue
        if "which option would you like" in lower:
            continue
        kept.append(ln)

    if not kept:
        return "I found a few relevant options."

    return kept[0]


def _render_option_line(index: int, option: Dict[str, Any]) -> str:
    title = _clean_user_title(option.get("title") or f"Option {index}")
    uri = option.get("uri")
    snippet = option.get("snippet") or option.get("description") or option.get("text") or ""
    snippet = _clean_user_snippet(snippet)

    title_part = f"[{title}]({uri})" if uri else str(title)
    if snippet:
        return f"Option {index}: {title_part} — {snippet}"
    return f"Option {index}: {title_part}"

# Format the answer for a choice flow, including the intro and options, based on the tool output. Only used when requires_user_choice is true. For non-choice answers, we want to keep it more concise and just give the direct answer without re-listing options.
def _format_choice_answer(answer: str, tool_output: Dict[str, Any]) -> str:
    options = tool_output.get("options") or []
    if not options:
        return answer

    intro = _pick_intro_from_answer(answer)
    lines = [intro, "", "Here are your options:"]

    max_options = min(5, len(options))
    for i in range(max_options):
        lines.append(_render_option_line(i + 1, options[i]))

    if max_options == 1:
        lines.append("")
        lines.append("Which option would you like more details on? Say ‘option 1’.")
    else:
        lines.append("")
        lines.append(
            f"Which option would you like more details on? Say ‘{_option_label(1)}’ or ‘{_option_label(max_options)}’."
        )

    return "\n".join(lines).strip()

# Check if pending action is expired based on created_at and ttl_sec. If expired, return True to indicate it should be cleared.
def _pending_is_expired(pending: Optional[Dict[str, Any]]) -> bool:
    if not pending:
        return False
    created_at = pending.get("created_at")
    ttl_sec = pending.get("ttl_sec")
    if created_at is None:
        return False
    if ttl_sec is None:
        ttl_sec = _PENDING_TTL_DEFAULT.get(pending.get("type", ""), 300)
    return (time.time() - float(created_at)) > float(ttl_sec)

def _clear_pending(db: Session, cid: str, meta: Dict[str, Any], reason: str) -> None:
    meta["pending_action"] = None
    set_metadata(db, cid, meta)
    log_event(db, cid, meta.get("user_id"), "pending_cleared", {"reason": reason})


# Load user context (e.g., name) to personalize responses. Cache for 5 minutes to avoid DB hits on every turn.
def _get_user_ctx(db: Session, user_id: Optional[str]) -> Dict[str, Any]:
    if not user_id:
        return {}
    ck = make_key("user_ctx", user_id)
    cached = cache().get(ck)
    if cached:
        return cached

    u = db.query(User).filter(User.id == user_id).first()
    ctx = {"full_name": u.full_name if u else None}
    cache().set(ck, ctx, ttl_seconds=300)
    return ctx


# Try to give a direct answer if we can, to minimize unnecessary multi-turn when the tool already gives a good answer or when the user is asking for a link.
def _direct_answer(tool: str, tool_output: Dict[str, Any]) -> Optional[str]:
    if tool_output.get("message"):
        return str(tool_output["message"])

    if tool_output.get("summary") and not tool_output.get("requires_user_choice"):
        return str(tool_output["summary"])

    if tool_output.get("error") and tool_output.get("message"):
        return str(tool_output["message"])

    if tool == "space_booking" and tool_output.get("requires_time"):
        return str(tool_output.get("message", "Please give a time like 3pm or 15:00."))

    return None

# Compose the final answer to return to the user, based on the tool output and question.
def _compose_answer(question: str, tool: str, tool_output: Dict[str, Any], history: list[dict], user_ctx: Dict[str, Any]) -> str:
    messages = [{"role": "system", "content": ANSWER_SYSTEM}]

    # getting the users name to be mentioned in the application
    if user_ctx.get("full_name"):
        first = user_ctx["full_name"].split(' ')[0]
        messages.append({"role": "system", "content": f"The user's first name is {first}. You may address them by name occasionally."})


    for h in history[-8:]:
        messages.append({"role": h["role"], "content": h["content"]})

    messages.append(
        {
            "role": "user",
            "content": f"""
User question: {question}

Tool used: {tool}
Tool output (JSON):
{json.dumps(tool_output, ensure_ascii=False)}

Write a short, spoken-style answer.
- Give the direct answer first.
- Never say "Here are your options".
- Never expose internal labels like "Seed Library:" or "Seed Doc:".
- If there is a relevant link, include it naturally.
- If there is an error, explain the next step briefly.
""".strip(),
        }
    )

    resp = client.chat.completions.create(
        model=LLM_MODEL,
        messages=messages,
        temperature=0.2,
        max_tokens=250,
    )
    return resp.choices[0].message.content.strip()



# Main orchestrator function
def run(
    db: Session,
    question: str,
    user_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    channel: str = "web",
) -> Dict[str, Any]:
    # ensure conversation exists
    cid = ensure_conversation(db, user_id=user_id, channel=channel, conversation_id=conversation_id)

    # load metadata + pending
    meta = get_metadata(db, cid) or {}
    pending = meta.get("pending_action")

    # attach user_id into meta for event logs (optional)
    meta["user_id"] = user_id

    # Expire old pending actions
    if _pending_is_expired(pending):
        _clear_pending(db, cid, meta, reason="pending_expired")
        pending = None

    # Skip stale non-booking option picks when user asks a new question.
    if pending and pending.get("type") != "space_booking":
        keep_pending_for_link = pending.get("type") == "rag_pick" and _is_link_request(question)
        if _looks_like_new_question(question):
            if not keep_pending_for_link:
                _clear_pending(db, cid, meta, reason="topic_switched_from_pending_non_booking")
                pending = None

    # store user message
    append_message(db, cid, role="user", content=question)

    # route (router already ignores rag_pick/booking unless user is selecting/continuing)
    routing = route(question, pending_action=pending)
    tool = routing["tool"]
    log_event(db, cid, user_id, "intent_detected", {"tool": tool, "reason": routing.get("reason")})

    tool_output: Dict[str, Any] = {}
    t0 = time.time()
    q_lower = (question or "").lower()
    selection = _extract_option_number(question)

    # If user switched topic away from a pending flow, clear it (unless they are asking to repeat options)
    if pending and pending.get("type") == "rag_pick" and tool != "rag":
        if selection is None and not _REPEAT_OPTIONS_RE.search(question or ""):
            _clear_pending(db, cid, meta, reason="topic_switched_from_rag_pick")
            pending = None

    if pending and pending.get("type") == "space_booking" and tool != "space_booking":
        if selection is None and not _REPEAT_OPTIONS_RE.search(question or ""):
            _clear_pending(db, cid, meta, reason="topic_switched_from_space_booking")
            pending = None

    if pending and pending.get("type") == "library_pick" and tool != "library":
        if selection is None and not _REPEAT_OPTIONS_RE.search(question or ""):
            _clear_pending(db, cid, meta, reason="topic_switched_from_library_pick")
            pending = None

    # handle pending rag pick (user choosing option OR asking to repeat options)
    if tool == "rag" and pending and pending.get("type") == "rag_pick":
        options = pending.get("options", [])

        if _REPEAT_OPTIONS_RE.search(question or ""):
            tool_output = {
                "message": "Sure — here are the options again. Which one should I open?",
                "options": options,
                "requires_user_choice": True,
            }
        elif _is_link_request(question) and options:
            chosen = options[0]
            tool_output = {
                "chosen_option": 1,
                "chosen": chosen,
                "summary": "Here’s the official link.",
            }
            meta["pending_action"] = None
            set_metadata(db, cid, meta)
            pending = None
        elif selection is None or selection < 1 or selection > len(options):
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
                "summary": "Got it — here’s the info for that option.",
            }
            # clear pending action once chosen
            meta["pending_action"] = None
            set_metadata(db, cid, meta)
            pending = None

    if not tool_output and tool == "library" and pending and pending.get("type") == "library_pick":
        options = pending.get("options", [])

        if _REPEAT_OPTIONS_RE.search(question or ""):
            tool_output = {
                "message": "Sure — here are the library options again. Which one would you like?",
                "options": options,
                "requires_user_choice": True,
            }
        elif selection is None or selection < 1 or selection > len(options):
            tool_output = {
                "error": "missing_selection",
                "message": "Please say something like 'option 1' or 'the second one'.",
                "options": options,
                "requires_user_choice": True,
            }
        else:
            chosen = options[selection - 1]
            availability = "available now" if chosen.get("is_available") else "currently unavailable"
            location = chosen.get("location") or "location not listed"

            tool_output = {
                "chosen_option": selection,
                "chosen": chosen,
                "summary": (
                    f"{chosen.get('title')} by {chosen.get('author')} is {availability}. "
                    f"It’s in {location}."
                ),
            }
            meta["pending_action"] = None
            set_metadata(db, cid, meta)
            pending = None

    # handle booking confirmation step (select, repeat options, or cancel)
    if not tool_output and tool == "space_booking" and pending and pending.get("type") == "space_booking":
        step = pending.get("step", "choose_space")
        options = pending.get("options", [])

        # user was asked for time
        if step == "need_time":
            tool_output = space_booking_tool.run_set_time(db, question, pending)
            if tool_output.get("requires_time"):
                # still waiting for a valid time
                meta["pending_action"] = {**pending, "step": "need_time", "target_date": tool_output.get("target_date")}
                set_metadata(db, cid, meta)
            elif tool_output.get("requires_alt_slot_confirm"):
                meta["pending_action"] = {
                    **pending,
                    "step": "alt_slot_time",
                    "alt_start_time": tool_output["alt_start_time"],
                    "alt_end_time": tool_output["alt_end_time"],
                    "alt_options": tool_output.get("alt_options", []),
                    "location_hint": tool_output.get("location_hint"),
                }
                set_metadata(db, cid, meta)
            elif tool_output.get("requires_user_choice"):
                meta["pending_action"] = {
                    "type": "space_booking",
                    "step": "choose_space",
                    "created_at": time.time(),
                    "ttl_sec": _PENDING_TTL_DEFAULT["space_booking"],
                    "start_time": tool_output["start_time"],
                    "end_time": tool_output["end_time"],
                    "options": tool_output.get("options", []),
                }
                set_metadata(db, cid, meta)

        # we suggested an alternative slot (time-level) because slot was fully booked
        elif step == "alt_slot_time":
            if _YES_RE.search(question or ""):
                # accept alt slot -> move user into choosing a space for that alt slot
                alt_opts = pending.get("alt_options", [])
                tool_output = {
                    "summary": "Cool — here are the available spaces for the next slot. Which one should I book?",
                    "start_time": pending["alt_start_time"],
                    "end_time": pending["alt_end_time"],
                    "options": alt_opts,
                    "requires_user_choice": True,
                }
                meta["pending_action"] = {
                    "type": "space_booking",
                    "step": "choose_space",
                    "created_at": time.time(),
                    "ttl_sec": _PENDING_TTL_DEFAULT["space_booking"],
                    "start_time": pending["alt_start_time"],
                    "end_time": pending["alt_end_time"],
                    "options": alt_opts,
                }
                set_metadata(db, cid, meta)
            elif _NO_RE.search(question or ""):
                meta["pending_action"] = {"type": "space_booking", "step": "need_time", "created_at": time.time(), "ttl_sec": _PENDING_TTL_DEFAULT["space_booking"]}
                set_metadata(db, cid, meta)
                tool_output = {"summary": "No worries — what time would you like instead? (e.g. 3pm / 15:00)"}
            else:
                tool_output = {"summary": "Say “yes” to book the next available slot, or “no” to choose a different time."}

        # normal choose-space step
        else:
            if _CANCEL_RE.search(question or ""):
                meta["pending_action"] = None
                set_metadata(db, cid, meta)
                pending = None
                tool_output = {"cancelled": True, "summary": "No problem — I’ve cancelled that booking request."}

            elif _REPEAT_OPTIONS_RE.search(question or ""):
                tool_output = {
                    "message": "Sure — here are the available spaces again. Which option would you like to book?",
                    "options": options,
                    "requires_user_choice": True,
                    "start_time": pending.get("start_time"),
                    "end_time": pending.get("end_time"),
                }

            elif selection is None:
                tool_output = {
                    "error": "missing_selection",
                    "message": "Please say something like 'book option 2'.",
                    "options": options,
                    "requires_user_choice": True,
                }

            else:
                if not user_id:
                    tool_output = {
                        "error": "missing_user",
                        "message": "Please sign in so I can confirm the booking. Then say 'book option 2'.",
                        "options": options,
                        "requires_user_choice": True,
                    }
                else:
                    tool_output = space_booking_tool.run_confirm(db, user_id=user_id, selection_index=selection, pending=pending)

                    # if confirm hit overlap and suggested alt slot for same space
                    if tool_output.get("requires_alt_slot_confirm") and tool_output.get("alt_space"):
                        meta["pending_action"] = {
                            "type": "space_booking",
                            "step": "alt_slot_space",
                            "created_at": time.time(),
                            "ttl_sec": _PENDING_TTL_DEFAULT["space_booking"],
                            "alt_space": tool_output["alt_space"],
                            "alt_start_time": tool_output["alt_start_time"],
                            "alt_end_time": tool_output["alt_end_time"],
                        }
                        set_metadata(db, cid, meta)

                    if tool_output.get("confirmed"):
                        meta["pending_action"] = None
                        set_metadata(db, cid, meta)
                        pending = None

    # handle alt slot confirm for same space (race condition)
    if not tool_output and tool == "space_booking" and pending and pending.get("type") == "space_booking" and pending.get("step") == "alt_slot_space":
        if _YES_RE.search(question or ""):
            tool_output = space_booking_tool.run_confirm_alt_slot(db, user_id=user_id, pending=pending)
            if tool_output.get("confirmed"):
                meta["pending_action"] = None
                set_metadata(db, cid, meta)
        elif _NO_RE.search(question or ""):
            meta["pending_action"] = {"type": "space_booking", "step": "need_time", "created_at": time.time(), "ttl_sec": _PENDING_TTL_DEFAULT["space_booking"]}
            set_metadata(db, cid, meta)
            tool_output = {"summary": "Okay — what time would you like instead? (e.g. 3pm / 15:00)"}
        else:
            tool_output = {"summary": "Say “yes” to book the next available slot, or “no” to choose a different time."}

    #  normal tool execution (only if we haven't produced output from pending handlers)
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

        elif tool == "library":
            tool_output = library_tool.run(db, question)

            if tool_output.get("requires_user_choice"):
                meta["pending_action"] = {
                    "type": "library_pick",
                    "created_at": time.time(),
                    "ttl_sec": _PENDING_TTL_DEFAULT["library_pick"],
                    "options": tool_output.get("options", []),
                    "original_question": question,
                }
                set_metadata(db, cid, meta)

        elif tool == "space_booking":
            # new booking find step
            if not user_id:
                tool_output = {"error": "missing_user", "message": "Please sign in so I can book a space for you."}
            else:
                tool_output = space_booking_tool.run_find(db, question)
                if tool_output.get("requires_time"):
                    meta["pending_action"] = {
                        "type": "space_booking",
                        "step": "need_time",
                        "created_at": time.time(),
                        "ttl_sec": _PENDING_TTL_DEFAULT["space_booking"],
                        "target_date": tool_output.get("target_date"),
                        "location_hint": tool_output.get("location_hint"),
                    }
                    set_metadata(db, cid, meta)

                elif tool_output.get("requires_alt_slot_confirm"):
                    meta["pending_action"] = {
                        "type": "space_booking",
                        "step": "alt_slot_time",
                        "created_at": time.time(),
                        "ttl_sec": _PENDING_TTL_DEFAULT["space_booking"],
                        "alt_start_time": tool_output["alt_start_time"],
                        "alt_end_time": tool_output["alt_end_time"],
                        "alt_options": tool_output.get("alt_options", []),
                        "location_hint": tool_output.get("location_hint"),
                    }
                    set_metadata(db, cid, meta)

                elif tool_output.get("requires_user_choice"):
                    meta["pending_action"] = {
                        "type": "space_booking",
                        "step": "choose_space",
                        "created_at": time.time(),
                        "ttl_sec": _PENDING_TTL_DEFAULT["space_booking"],
                        "start_time": tool_output.get("start_time"),
                        "end_time": tool_output.get("end_time"),
                        "options": tool_output.get("options", []),
                    }
                    set_metadata(db, cid, meta)


        else:
            # RAG fallback
            tool_output = rag_tool.run(db, question)

            hits = tool_output.get("hits") or []
            options = _build_rag_options(hits, max_options=5)

            if options and _should_require_rag_choice(question, hits):
                tool_output["options"] = options
                tool_output["requires_user_choice"] = True
                meta["pending_action"] = {
                    "type": "rag_pick",
                    "created_at": time.time(),
                    "ttl_sec": _PENDING_TTL_DEFAULT["rag_pick"],
                    "options": options,
                    "original_question": question,
                }
                set_metadata(db, cid, meta)
            elif options:
                top = options[0]
                tool_output["chosen"] = top
                if top.get("snippet"):
                    tool_output["summary"] = top.get("snippet")

    latency_ms = int((time.time() - t0) * 1000)
    log_event(db, cid, user_id, "tool_result", {"tool": tool, "latency_ms": latency_ms})

    user_ctx = _get_user_ctx(db, user_id)

    direct = _direct_answer(tool, tool_output)
    if direct:
        answer = direct
    else:
        # compose final response via LLM
        history = recent_messages(db, cid, limit=12)
        answer = _compose_answer(question, tool, tool_output, history, user_ctx)
        if tool_output.get("requires_user_choice") and tool_output.get("options"):
            answer = _format_choice_answer(answer, tool_output)
        answer = _ensure_link_in_answer(answer, tool_output, question)


    # store assistant message
    append_message(
        db,
        cid,
        role="assistant",
        content=answer,
        tool_name=tool,
        tool_payload=tool_output,
        latency_ms=latency_ms,
    )

    try:
        log_query_analytics(
            db,
            conversation_id=cid,
            user_id=user_id,
            channel=channel,
            question_text=question,
            detected_intent=tool,
            route_reason=routing.get("reason"),
            tool_name=tool,
            tool_output=tool_output,
            latency_ms=latency_ms,
            pending_action=meta.get("pending_action"),
            selected_option=selection,
        )
    except Exception:
        # analytics logging should never break the assistant
        pass

    return {
        "conversation_id": cid,
        "tool": tool,
        "answer": answer,
        "tool_output": tool_output,
    }
