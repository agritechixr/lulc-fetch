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


@router.get("/api/workflows/schedules")
def workflow_schedules():
    """Every schedule, its next run, and which are due now (the app runs those)."""
    from .. import workflows
    return {"schedules": workflows.schedules(), "now": __import__("time").time()}


class ScheduleBody(BaseModel):
    schedule: dict


@router.put("/api/workflows/{wid}/schedule")
def workflow_schedule_set(wid: str, req: ScheduleBody):
    from .. import workflows
    try:
        return workflows.set_schedule(wid, req.schedule)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))


@router.delete("/api/workflows/{wid}/schedule")
def workflow_schedule_delete(wid: str):
    from .. import workflows
    workflows.delete_schedule(wid)
    return {"ok": True}


class RanBody(BaseModel):
    ok: bool
    message: str = Field("", max_length=2000)
    alert: bool = False


@router.post("/api/workflows/{wid}/schedule/ran")
def workflow_schedule_ran(wid: str, req: RanBody):
    """A scheduled run finished (or failed): its next run is set."""
    from .. import workflows
    try:
        return workflows.mark_ran(wid, req.ok, req.message, req.alert)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))


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
