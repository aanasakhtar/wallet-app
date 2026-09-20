"""
Admin endpoints used only for the Part E experiment.

Protected by a shared token so an exposed instance can't be trivially
degraded. In a real system this would sit behind proper RBAC, or would not
exist in the production build at all - worth saying so in the report.
"""
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel

from ..config import settings
from ..faults import fault_injector

router = APIRouter(prefix="/admin", tags=["admin"])


class FaultUpdate(BaseModel):
    latency_enabled: Optional[bool] = None
    latency_ms: Optional[int] = None
    latency_every_n: Optional[int] = None
    error_enabled: Optional[bool] = None
    error_every_n: Optional[int] = None


def _check_token(token: Optional[str]) -> None:
    if token != settings.admin_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin token"
        )


@router.get("/faults")
def get_faults(x_admin_token: Optional[str] = Header(default=None)):
    _check_token(x_admin_token)
    return fault_injector.get_config()


@router.post("/faults")
def set_faults(payload: FaultUpdate, x_admin_token: Optional[str] = Header(default=None)):
    _check_token(x_admin_token)
    return fault_injector.update(**payload.model_dump())


@router.post("/faults/reset")
def reset_faults(x_admin_token: Optional[str] = Header(default=None)):
    """Single call that turns every injected fault off again."""
    _check_token(x_admin_token)
    return fault_injector.reset()
