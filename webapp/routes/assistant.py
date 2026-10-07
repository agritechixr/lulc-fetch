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
    messages: list[dict] = Field(min_length=1, max_length=40)
    context: dict = Field(default_factory=dict)


@router.post("/api/assistant/plan")
def assistant_plan(req: PlanRequest):
    """The request (and the conversation so far) planned as a workflow of the app's tools; nothing runs."""
    import requests as _rq

    from .. import assistant
    try:
        return assistant.plan(req.messages, req.context)
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


@router.get("/api/assistant/catalog")
def assistant_catalog():
    """The tools the Assistant may plan with, as it is told about them."""
    from .. import assistant
    return {"text": assistant.catalog_text()}
