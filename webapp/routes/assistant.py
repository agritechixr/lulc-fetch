"""The Assistant (Analysis ▸ Tools ▸ Assistant): its status and settings, downloading a local model, and planning a request
as a workflow. The plan runs in the app like any workflow, after the user checks it. Logic in webapp/assistant.py."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..core import jobs

router = APIRouter()


@router.get("/api/assistant/status")
def assistant_status():
    from .. import assistant
    return assistant.status()


class AssistantSettings(BaseModel):
    provider: str = Field(pattern="^(ollama|claude)$")
    model: str = Field("", max_length=100)
    ollama_url: str = Field("", max_length=200)


@router.put("/api/assistant/settings")
def assistant_settings(req: AssistantSettings):
    from .. import assistant
    try:
        assistant.save_settings(req.provider, req.model, req.ollama_url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return assistant.status()


class PullRequest(BaseModel):
    model: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._:/-]+$")


@router.post("/api/assistant/pull")
def assistant_pull(req: PullRequest):
    """Download a local model into Ollama, as a background job (progress, Cancel)."""
    from .. import assistant
    if not assistant.status()["ollama"]["running"]:
        raise HTTPException(400, "Ollama isn't running → install it from ollama.com and start it, then try again")
    return jobs.submit("assistant-model", f"Download the model {req.model}", {"model": req.model}, lambda job: assistant.pull(req.model, job)).to_dict()


class PlanRequest(BaseModel):
    messages: list[dict] = Field(min_length=1, max_length=60)
    context: dict = Field(default_factory=dict)
    conv_id: str = Field("", max_length=40)


def _model_errors(fn):
    """Run a planning call; the models' failures as messages that say what to do."""
    import requests as _rq
    try:
        return fn()
    except ValueError as e:
        raise HTTPException(400, str(e))
    except _rq.RequestException as e:
        raise HTTPException(502, f"The local model didn't answer → is Ollama running? ({e})")
    except Exception as e:  # noqa: BLE001 — the cloud SDK's errors (key, rate limit, network) as a message
        name = type(e).__name__
        if name in ("AuthenticationError", "PermissionDeniedError"):
            raise HTTPException(401, "Claude refused the API key → check it in Credentials (Anthropic)")
        if name == "RateLimitError":
            raise HTTPException(429, "Claude is busy for your key (rate limit) → wait a minute and try again")
        if name in ("APIConnectionError", "APITimeoutError"):
            raise HTTPException(502, "Couldn't reach Claude → check the internet connection, or use the local model")
        raise


@router.post("/api/assistant/plan")
def assistant_plan(req: PlanRequest):
    """The request (and the conversation so far) planned as a workflow of the app's tools; nothing runs."""
    from .. import assistant
    return _model_errors(lambda: assistant.plan(req.messages, req.context, req.conv_id))


class ContinueRequest(PlanRequest):
    workflow: dict
    done: list[dict] = Field(default_factory=list, max_length=50)   # [{title, endpoint, body, observation}]
    failed: dict | None = None                                       # {title, endpoint, body, error | warnings, history}


@router.post("/api/assistant/continue")
def assistant_continue(req: ContinueRequest):
    """Replan after a step failed or made something wrong: the steps still to run (the done ones are kept)."""
    from .. import assistant
    return _model_errors(lambda: assistant.continue_plan(req.messages, req.context, req.workflow, req.done, req.failed, req.conv_id))


class ObserveRequest(BaseModel):
    paths: list[str] = Field(max_length=50)


@router.post("/api/assistant/observe")
def assistant_observe(req: ObserveRequest):
    """What a step made, looked at (bands and value ranges, rows and columns), with warnings when it looks wrong."""
    from .. import assistant_data
    return {"files": assistant_data.describe_many(req.paths)}


@router.get("/api/assistant/memory")
def assistant_memory():
    from .. import assistant_data as d
    return {"notes": d.notes(), "pitfalls": d.pitfalls(per_tool=5), "conversations": d.conversations(), "folder": str(d.folder())}


class NotesBody(BaseModel):
    notes: str = Field("", max_length=20000)


@router.put("/api/assistant/memory/notes")
def assistant_notes(req: NotesBody):
    from .. import assistant_data
    return {"notes": assistant_data.save_notes(req.notes)}


@router.delete("/api/assistant/memory/pitfalls")
def assistant_pitfalls_clear():
    from .. import assistant_data
    assistant_data.clear_pitfalls()
    return {"ok": True}


class Lesson(BaseModel):
    endpoint: str = Field(max_length=200)
    error: str = Field(max_length=2000)
    fix: str = Field("", max_length=2000)
    body: dict = Field(default_factory=dict)


@router.post("/api/assistant/memory/lesson")
def assistant_lesson(req: Lesson):
    """A step that failed, then worked after a fix: kept so later plans avoid the error."""
    from .. import assistant_data
    assistant_data.record_error(req.endpoint, req.body, req.error, req.fix)
    return {"ok": True}


class LogEvent(BaseModel):
    conv_id: str = Field(max_length=40)
    event: dict


@router.post("/api/assistant/log")
def assistant_log(req: LogEvent):
    """An event of a conversation (a step done or failed, a run finished) for its transcript."""
    from .. import assistant_data
    assistant_data.log(req.conv_id, req.event)
    return {"ok": True}


@router.get("/api/assistant/conversations/{conv_id}")
def assistant_conversation(conv_id: str):
    from .. import assistant_data
    try:
        return {"events": assistant_data.conversation(conv_id)}
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(404, str(e))


@router.delete("/api/assistant/conversations/{conv_id}")
def assistant_conversation_delete(conv_id: str):
    from .. import assistant_data
    assistant_data.delete_conversation(conv_id)
    return {"ok": True}


@router.get("/api/assistant/catalog")
def assistant_catalog():
    """The tools the Assistant may plan with, as it is told about them."""
    from .. import assistant
    return {"text": assistant.catalog_text()}
