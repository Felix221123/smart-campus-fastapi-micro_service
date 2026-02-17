# src/module/assistant_views.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.database import get_db
from src.module.schemas import ORMBase

from src.llm.orchestrator import run as llm_run
from typing import Optional
import os
import httpx
from fastapi import APIRouter, HTTPException
import re

router = APIRouter(tags=["Assistant"])


class AskRequest(ORMBase):
    question: str
    conversation_id: Optional[str] = None
    user_id: Optional[str] = None
    channel: str = "web"


@router.post("/ask")
def ask(payload: AskRequest, db: Session = Depends(get_db)):
    """
    Voice-optimized endpoint that returns cleaner responses
    without markdown formatting for better text-to-speech
    """
    result = llm_run(
        db=db,
        question=payload.question,
        user_id=payload.user_id,
        conversation_id=payload.conversation_id,
        channel="web"
    )

    # Clean up the response for voice output
    if result and "answer" in result:
        answer = result["answer"]

        # Remove markdown links but keep the text
        # [text](url) -> "text"
        answer = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', answer)

        # Remove markdown formatting
        answer = re.sub(r'#+\s', '', answer)  # Headers
        answer = re.sub(r'\*\*([^\*]+)\*\*', r'\1', answer)
        answer = re.sub(r'\*([^\*]+)\*', r'\1', answer)
        answer = re.sub(r'`([^`]+)`', r'\1', answer)

        # Remove all newlines and replace with spaces
        answer = answer.replace('\n', ' ')

        # Remove multiple spaces
        answer = re.sub(r'\s+', ' ', answer)

        # Simplify option formatting for voice
        # "Option 1:" -> "Option 1."
        answer = re.sub(r'Option (\d+):', r'Option \1.', answer)

        # Make it more conversational for voice
        if "Option 1" in answer:
            # Add a natural intro
            answer = "Here are your options. " + answer
            # Add natural ending
            if not answer.endswith("?"):
                answer += " Which option would you like?"

        answer = answer.strip()

        result["answer"] = answer

    return result



@router.post("/realtime/client-secret")
async def create_realtime_client_secret():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not set")

    # Minimal session config (Realtime speech-to-speech capable model)
    payload = {
        "session": {
            "type": "realtime",
            "model": "gpt-realtime",
        }
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            "https://api.openai.com/v1/realtime/client_secrets",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )

    if r.status_code >= 400:
        raise HTTPException(status_code=500, detail={"openai_error": r.text})

    data = r.json()
    # Docs show returning data.value (and sometimes expires_at)
    return {"value": data["value"], "expires_at": data.get("expires_at")}
