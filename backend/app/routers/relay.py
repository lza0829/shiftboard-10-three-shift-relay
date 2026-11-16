from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services import relay as svc

router = APIRouter(prefix="/relay-plans", tags=["接力交接"])


class PlanBody(BaseModel):
    shift_ids: list[int] = Field(..., description="按班次顺序排列，至少三个班次")
    name: str | None = Field(default=None, max_length=80)


@router.get("")
def get_plans():
    return svc.list_plans()


@router.post("")
def create_plan(body: PlanBody):
    return svc.create_plan(body.name, body.shift_ids)


@router.get("/{plan_id}")
def get_plan(plan_id: int):
    return svc.get_plan(plan_id)


@router.post("/{plan_id}/segments/start")
def start_next(plan_id: int):
    return svc.start_next_segment(plan_id)


@router.post("/{plan_id}/abort")
def abort(plan_id: int):
    return svc.abort_plan(plan_id)
