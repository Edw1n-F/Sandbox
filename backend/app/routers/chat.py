"""Chat endpoints: one question about a run, answered as a grounded card.

Two routes, same answer:

- `POST /api/scenes/{scene_id}/chat`          -- the card, once.
- `POST /api/scenes/{scene_id}/chat/stream`   -- SSE: steps, deltas, the card.

Both fall back to a template card when no model is configured, and both label
which happened, so the UI never presents code-assembled text as model output.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, status

from app.agent import chat_service
from app.schemas.chat import ChatRequest

router = APIRouter(prefix="/scenes/{scene_id}", tags=["chat"])


@router.post("/chat", summary="Answer one question about a run")
def chat(scene_id: str, body: ChatRequest) -> dict:
    """The answer card, complete. Use the stream route when you want the steps."""
    facts = chat_service.build_facts(body.run)
    try:
        card = chat_service.answer(body.question, facts, body.history)
    except Exception as exc:  # a model must never take the endpoint down
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"chat failed: {exc}"
        ) from exc
    return {"sceneId": scene_id, "answer": card.model_dump()}


@router.post("/chat/stream", summary="Answer one question, streaming the steps")
def chat_stream(scene_id: str, body: ChatRequest):
    """Server-sent events, one JSON object per event:

    `run_started`, `step_started`, `step_finished`, `delta`, `grounding`,
    `answer`, `error`, `done`. The stream always ends with exactly one `done`.
    """
    from fastapi.responses import StreamingResponse

    facts = chat_service.build_facts(body.run)

    def generate():
        try:
            for event in chat_service.stream_events(body.question, facts, body.history):
                yield f"event: {event['event']}\ndata: {json.dumps(event)}\n\n"
        except Exception as exc:  # keep the stream well-formed even on a hard failure
            yield f"event: error\ndata: {json.dumps({'event': 'error', 'message': str(exc), 'recoverable': False})}\n\n"
            yield f"event: done\ndata: {json.dumps({'event': 'done', 'status': 'failed', 'ms': 0})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
    )
