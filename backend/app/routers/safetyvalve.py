"""安全阀校验接口：维护安全阀台账，覆盖安排校验、登记合格、申请报废与排期规则调整。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.safetyvalve import STATUS_ORDER, SafetyvalveService

router = APIRouter(prefix="/api/safetyvalve", tags=["安全阀校验"])

service = SafetyvalveService()

STATUSES = STATUS_ORDER


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按安全阀编号检索"),
    status: str | None = Query(default=None, description="校验合格、即将到期、待校验、已报废"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按安全阀编号与状态过滤校验清单；待校验、即将到期按排期规则前置。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/stats")
def stats() -> dict[str, int]:
    """各状态阀数与超年限阀数，口径与清单列表完全一致。"""
    return service.stats()


@router.get("/rules")
def get_rules() -> dict[str, Any]:
    """读取当前排期判定规则：整定压力档位、预警天数、使用年限。"""
    return service.get_rules()


@router.put("/rules", response_model=ActionResult)
def update_rules(payload: EntryPayload) -> ActionResult:
    """调整判定规则；规则生效后全部阀门及其校验记录的下次校验日立即重算。"""
    rules, message = service.update_rules(payload.values)
    if rules is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=rules)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出安全阀校验清单：返回全量数据（含排期说明）。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "safetyvalve", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条安全阀明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"安全阀 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条安全阀，缺字段或日期/压力格式不对时说明原因，而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"参数不合规：{'、'.join(missing)}")
    return ActionResult(ok=True, message="安全阀已登记，下次校验日已按档位规则计算", entry=entry)


@router.put("/{entry_id}", response_model=ActionResult)
def update_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """修改阀门台账（如整定压力）；修改后立即按新档位重算下次校验日。"""
    entry, message = service.update_entry(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条安全阀执行安排校验、登记合格、申请报废；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.pop("action", "") or "").strip()
    entry, message = service.run_action(entry_id, action, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
