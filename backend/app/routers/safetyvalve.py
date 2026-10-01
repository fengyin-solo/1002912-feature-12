"""安全阀校验接口：阀门台账、合格校验记录、按规则重算下次校验日。

排期状态、提醒缘由、使用年限提示全部由 safetyvalve_rules 统一派生，
台账列表与校验清单共用同一结论。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, SafetyvalvePageResult
from app.services import safetyvalve_rules as rules
from app.services.safetyvalve import MODULE, RECORDS_MODULE, SafetyvalveService

router = APIRouter(prefix="/api/safetyvalve", tags=["安全阀校验"])

service = SafetyvalveService()

LIST_FIELDS = [
    "安全阀编号", "所属设备", "公称通径", "整定压力", "压力档位",
    "校验日期", "下次校验日", "校验结论", "安全阀状态", "提醒缘由",
]
STATUSES = rules.VALID_STATUSES


def _rules_description() -> dict[str, Any]:
    return {
        "pressureTiers": [
            {"minPressureMp": lower, "name": name, "cycleMonths": months}
            for lower, name, months in rules.PRESSURE_TIERS
        ],
        "defaultServiceYears": rules.DEFAULT_SERVICE_YEARS,
        "warnDays": rules.WARN_DAYS,
        "note": "下次校验日=上次校验日期+整定压力档位周期；改档位或调规则后需执行重算",
    }


@router.get("", response_model=SafetyvalvePageResult)
def list_entries(
    keyword: str | None = Query(default=None, description="按安全阀编号检索"),
    status: str | None = Query(default=None, description="校验合格、即将到期、校验超期、待校验、已报废"),
    page: int = 1,
    size: int = 20,
) -> SafetyvalvePageResult:
    """安全阀台账列表：在役阀门中已超期/7 天内临期的自动前置并附提醒缘由，已报废沉底且不参与排期。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total, summary = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return SafetyvalvePageResult(
        items=items, total=total, page=page, size=size,
        summary=summary, rules=_rules_description(),
    )


@router.get("/rules")
def get_rules() -> dict[str, Any]:
    """读取当前校验周期判定规则（压力档位、周期、年限、预警天数）。"""
    return _rules_description()


@router.post("/recalc", response_model=ActionResult)
def recalc_entries() -> ActionResult:
    """判定规则调整（或整定压力批量变更）后，按新规则重算全部在役阀门的下次校验日与排期状态。"""
    result = service.recalc()
    return ActionResult(
        ok=True,
        message=f"已按最新规则重算 {result['updated']}/{result['total']} 只安全阀的下次校验日",
        entry={"updated": result["updated"], "total": result["total"]},
    )


@router.get("/records")
def list_records(
    valve_id: int | None = Query(default=None, description="按安全阀台账 ID 过滤；不传则返回全部合格记录"),
) -> dict[str, Any]:
    """校验清单：全部合格登记记录。同阀同日重复登记的合格不会出现，只认第一次。"""
    records = service.list_records(valve_id)
    return {"module": RECORDS_MODULE, "total": len(records), "items": records}


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出安全阀台账视图与校验清单，两份数据同源，结论保持一致。"""
    items, total, summary = service.list_entries(page=1, size=10000)
    records = service.list_records()
    return {
        "module": MODULE,
        "total": total,
        "summary": summary,
        "rules": _rules_description(),
        "items": items,
        "records": records,
    }


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict[str, Any]:
    """读取单条安全阀明细（含排期状态与提醒缘由）；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"安全阀 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一只安全阀，缺字段或编号重复时说明原因而不是静默丢弃。"""
    entry, errors = service.create_entry(payload.values)
    if errors:
        return ActionResult(ok=False, message=f"{'；'.join(errors)}")
    return ActionResult(ok=True, message="安全阀已登记，尚未有合格校验记录，状态为待校验", entry=entry)


@router.patch("/{entry_id}", response_model=ActionResult)
def update_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """修改台账字段；整定压力变更后立即按新档位重算该阀的下次校验日。"""
    entry, message = service.update_entry(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条安全阀执行安排校验、登记合格（需带校验日期）、申请报废。

    登记合格时同阀同日重复登记只认第一次；已报废阀门不参与排期，动作会被拦下。
    """
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
