from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services import handover as svc

router = APIRouter(prefix="/handovers", tags=["交班"])


class StartBody(BaseModel):
    from_shift_id: int = Field(..., description="交班班次")
    to_shift_id: int = Field(..., description="接班班次")


class FailBody(BaseModel):
    reason: str = Field(..., min_length=1, max_length=200, description="失败原因")


@router.get("")
def get_handovers(relay_plan_id: int | None = None):
    return svc.list_handovers(relay_plan_id)


@router.get("/{handover_id}")
def get_one(handover_id: int):
    return svc.get_handover(handover_id)


@router.post("/start")
def start(body: StartBody):
    return svc.start_handover(body.from_shift_id, body.to_shift_id)


@router.post("/{handover_id}/complete")
def complete(handover_id: int):
    return svc.complete_handover(handover_id)


@router.post("/{handover_id}/fail")
def fail(handover_id: int, body: FailBody):
    return svc.fail_handover(handover_id, body.reason)


@router.post("/{handover_id}/close")
def close(handover_id: int):
    return svc.close_failed_handover(handover_id)
