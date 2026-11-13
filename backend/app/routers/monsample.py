"""监控采样时间线接口：调度执行或质量校验完成后回调生成读数，支持确认、忽略与失败重采。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.monsample import ALERT_STATUSES, SamplingService

router = APIRouter(prefix="/api/monsample", tags=["监控采样时间线"])

service = SamplingService()

LIST_FIELDS = ["采样编号", "指标编号", "监控对象", "采样周期", "采样批次", "指标值", "阈值版本", "告警状态"]
STATUSES = ALERT_STATUSES


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按采样批次或采样编号检索"),
    status: str | None = Query(default=None, description="告警状态：无告警、待确认、已确认、已忽略"),
    metric: str | None = Query(default=None, description="按指标编号过滤"),
    rule: str | None = Query(default=None, description="按质量规则编号过滤"),
    task: str | None = Query(default=None, description="按调度任务编号过滤"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按采样时间倒序返回监控读数时间线；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        keyword=keyword, status=status, metric=metric, rule=rule, task=task, page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("/callback", response_model=ActionResult)
def record_callback(payload: EntryPayload) -> ActionResult:
    """调度执行或质量校验完成后回调生成读数；同一批次重复回调幂等，只保留一份记录。"""
    entry, message = service.record_callback(payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出监控采样时间线：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "monsample", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条监控读数明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"监控读数 {entry_id} 不存在")
    return entry


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """确认告警、忽略告警（需处理人）或重采失败批次；不满足条件的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
