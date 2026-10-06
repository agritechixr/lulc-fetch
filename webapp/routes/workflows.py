"""Workflows (Analysis ▸ Tools ▸ Workflows): list, read, save, delete, and make one from History runs. Running a workflow
happens in the app, step by step, through each tool's own endpoint (with its progress, Cancel and History)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()


@router.get("/api/workflows")
def workflows_list():
    from .. import workflows
    return {"workflows": workflows.listing(), "folder": str(workflows.folder())}


@router.get("/api/workflows/{wid}")
def workflow_get(wid: str):
    from .. import workflows
    try:
        return workflows.get(wid)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(404, str(e))


class WorkflowBody(BaseModel):
    workflow: dict


@router.put("/api/workflows/{wid}")
def workflow_save(wid: str, req: WorkflowBody):
    from .. import workflows
    try:
        return workflows.save(None if wid == "new" else wid, req.workflow)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/api/workflows/{wid}")
def workflow_delete(wid: str):
    from .. import workflows
    try:
        workflows.delete(wid)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


class FromHistory(BaseModel):
    job_ids: list[str] = Field(min_length=1, max_length=50)
    name: str = ""


@router.post("/api/workflows/from-history")
def workflow_from_history(req: FromHistory):
    """A workflow made from History runs (not saved yet: the app shows it to name and save)."""
    from .. import workflows
    try:
        return workflows.from_history(req.job_ids, req.name)
    except ValueError as e:
        raise HTTPException(400, str(e))
