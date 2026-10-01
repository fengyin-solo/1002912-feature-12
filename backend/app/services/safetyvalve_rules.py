"""安全阀校验周期判定规则。

所有"下次校验日/排期状态/提醒缘由/使用年限"的口径都收在这一处，
阀门台账视图与校验清单共用本模块的派生结果，保证两边结论一致。

规则依据与约定：
- 依据 TSG 21F-2016《安全阀安全技术监察规程》及 TSG ZF001 对高风险
  安全阀从严校验的原则，按整定压力分档确定校验周期：
    * 高压档（整定压力 ≥ 10.0 MPa）：6 个月；
    * 中压档（1.6 MPa ≤ 整定压力 < 10.0 MPa）：12 个月；
    * 低压档（整定压力 < 1.6 MPa）：24 个月。
- 下次校验日 = 上次校验日期 + 所属档位周期（按日历月加算）。
- 距下次校验日不足 WARN_DAYS 天（含当天）的前置提示；已超期的同样前置。
- 使用年限默认 15 年（自投用日期起算），超年限的阀门另作提示。
判定规则调整后，调用 recalc_due_dates 即可让存量记录按新规则重算。
"""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta
from typing import Any

# (下限 MPa 含, 档位名称, 周期月数)；按从严到宽排列，命中第一条即止
PRESSURE_TIERS: list[tuple[float, str, int]] = [
    (10.0, "高压档", 6),
    (1.6, "中压档", 12),
    (0.0, "低压档", 24),
]
DEFAULT_SERVICE_YEARS = 15
WARN_DAYS = 7

SCRAPPED = "已报废"
STATUS_QUALIFIED = "校验合格"
STATUS_DUE_SOON = "即将到期"
STATUS_OVERDUE = "校验超期"
STATUS_PENDING = "待校验"  # 从未登记过合格记录
VALID_STATUSES = [STATUS_QUALIFIED, STATUS_DUE_SOON, STATUS_OVERDUE, STATUS_PENDING, SCRAPPED]

PRESSURE_FIELD = "整定压力"
LAST_DATE_FIELD = "校验日期"
NEXT_DATE_FIELD = "下次校验日"
RESULT_FIELD = "校验结论"
LEDGER_STATUS_FIELD = "安全阀状态"
COMMISSION_FIELD = "投用日期"
SERVICE_YEARS_FIELD = "使用年限"
TIER_FIELD = "压力档位"


def parse_pressure(value: Any) -> float | None:
    """把 "1.6MPa"、"1.6 MPa"、1.6 等写法解析成数值；无法识别时返回 None。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(match.group()) if match else None


def pressure_tier(pressure: Any) -> tuple[str | None, int | None, float | None]:
    """按整定压力返回 (档位名称, 周期月数, 解析后的压力值)；压力缺失/非法时档位为 None。"""
    value = parse_pressure(pressure)
    if value is None:
        return None, None, None
    for lower, name, months in PRESSURE_TIERS:
        if value >= lower:
            return name, months, value
    return None, None, value


def parse_date(value: Any) -> date | None:
    """解析 ISO 日期字符串；None、空串与非法值都按"无日期"处理。"""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def add_months(day: date, months: int) -> date:
    """按日历月加算，落在目标月没有的日期（如 2/29）顺延到该月最后一天。"""
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return day.replace(year=year, month=month, day=min(day.day, last_day))


def calc_next_date(last_date: Any, pressure: Any) -> date | None:
    """核心口径：上次校验日期 + 整定压力档位对应周期 = 下次校验日。

    任一输入缺失则无法排期，返回 None。
    """
    base = parse_date(last_date)
    _, cycle_months, _ = pressure_tier(pressure)
    if base is None or cycle_months is None:
        return None
    return add_months(base, cycle_months)


def service_limit(entry: dict[str, Any]) -> date | None:
    """按投用日期与使用年限算到期日；没有投用日期时无法判定，返回 None。"""
    commission = parse_date(entry.get(COMMISSION_FIELD))
    if commission is None:
        return None
    years = entry.get(SERVICE_YEARS_FIELD)
    years_value = parse_pressure(years)  # 复用数值解析：支持 "15年" 这类写法
    if years_value is None:
        years_value = DEFAULT_SERVICE_YEARS
    try:
        years_int = int(years_value)
    except (ValueError, OverflowError):
        years_int = DEFAULT_SERVICE_YEARS
    if years_int <= 0:
        years_int = DEFAULT_SERVICE_YEARS
    return add_months(commission, years_int * 12)


def derive_view(
    entry: dict[str, Any],
    records: list[dict[str, Any]] | None = None,
    *,
    today: date | None = None,
) -> dict[str, Any]:
    """以台账字段 + 合格校验记录派生展示视图。

    台账列表与校验清单都走这一个函数，确保两边给出的结论一致：
    - 已报废：不参与排期，状态固定「已报废」；
    - 无合格记录：状态「待校验」，提示尚未登记过合格校验；
    - 距下次校验日不足 WARN_DAYS 天：「即将到期」；
    - 已过下次校验日：「校验超期」；
    - 其余：「校验合格」；
    - 超过使用年限：不论校验状态，额外给出超年限提示。
    """
    today = today or date.today()
    records = records or []

    code = entry.get("安全阀编号", "")
    tier_name, _, pressure_value = pressure_tier(entry.get(PRESSURE_FIELD))
    last_record = latest_qualified(records)

    last_date_raw = last_record.get("校验日期") if last_record else entry.get(LAST_DATE_FIELD)
    last_date = parse_date(last_date_raw)
    # 排期一律以"当前台账上的整定压力档位"重算，改了整定压力立即按新档位走
    next_day = calc_next_date(last_date_raw, entry.get(PRESSURE_FIELD))

    reasons: list[str] = []
    scrapped = entry.get("status") == SCRAPPED or str(entry.get(LEDGER_STATUS_FIELD) or "") == SCRAPPED

    if scrapped:
        status = SCRAPPED
        next_day = None
    elif last_record is None and last_date is None:
        status = STATUS_PENDING
        reasons.append("从未登记过合格校验，需尽快安排首检")
    elif next_day is None:
        # 有校验日期但缺整定压力/档位，无法排期，按待校验处理并说明
        status = STATUS_PENDING
        if pressure_value is None:
            reasons.append("整定压力缺失或无法识别，无法确定校验周期，请补登后重算")
        else:
            reasons.append("上次校验日期缺失，无法推算下次校验日")
    else:
        delta = (next_day - today).days
        if delta < 0:
            status = STATUS_OVERDUE
            reasons.append(f"下次校验日 {next_day.isoformat()} 已超期 {-delta} 天")
        elif delta <= WARN_DAYS:
            status = STATUS_DUE_SOON
            reasons.append(f"距下次校验日 {next_day.isoformat()} 仅剩 {delta} 天，不足 {WARN_DAYS} 天预警线")
        else:
            status = STATUS_QUALIFIED
            reasons.append(f"下次校验日 {next_day.isoformat()}，剩余 {delta} 天")

    limit = service_limit(entry)
    expired = bool(limit and limit < today and not scrapped)
    if expired and limit is not None:
        reasons.append(f"已超过使用年限（{limit.isoformat()} 到期），建议停用并安排报废评估")

    if pressure_value is None and not scrapped:
        tier_label = "未分档"
    else:
        tier_label = tier_name or "未分档"

    view = dict(entry)
    view[TIER_FIELD] = tier_label
    view[LAST_DATE_FIELD] = last_date.isoformat() if last_date else (last_date_raw or "")
    view[NEXT_DATE_FIELD] = next_day.isoformat() if next_day else ""
    view[RESULT_FIELD] = last_record.get("校验结论") if last_record else (entry.get(RESULT_FIELD) or "")
    view[LEDGER_STATUS_FIELD] = status
    view["status"] = status
    view["提醒缘由"] = "；".join(reasons)
    view["超年限"] = expired
    view["年限到期日"] = limit.isoformat() if limit else ""
    view["合格次数"] = len(records)
    view["_days_left"] = (next_day - today).days if next_day else None
    return view


def latest_qualified(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """取最近一次合格校验作为排期基准（上次校验日期）。

    正常的周期校验每年都会产生新记录，下次校验日跟着最近一次合格日走；
    "同一只阀重复登记合格只认第一次"针对的是同一天的重复录入——
    那种情况在 service 层直接拒收，不会进到这里。同日期若存在多条，
    取最早登记的一条，保证重复数据不会改变结论。
    """
    qualified: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        if str(record.get("校验结论") or "") != "合格":
            continue
        if parse_date(record.get("校验日期")) is None:
            continue
        qualified.append((parse_date(record.get("校验日期")), index, record))
    if not qualified:
        return None
    # 日期降序；同日并列时取先登记（index 更小）的一条
    qualified.sort(key=lambda item: (item[0], -item[1]), reverse=True)
    return qualified[0][2]


def sort_key(view: dict[str, Any]) -> tuple[int, int, date | None, str]:
    """列表排序：已报废沉底；在役阀门里超期/临期前置，再按下次校验日升序。"""
    status = view.get("status")
    if status == SCRAPPED:
        urgency = 9
    elif status == STATUS_OVERDUE:
        urgency = 0
    elif status == STATUS_DUE_SOON:
        urgency = 1
    elif status == STATUS_PENDING:
        urgency = 2
    else:
        urgency = 3
    days_left = view.get("_days_left")
    days_int = days_left if isinstance(days_left, int) else 10**6
    return urgency, days_int, parse_date(view.get(NEXT_DATE_FIELD)), str(view.get("安全阀编号", ""))


def recalc_record(record: dict[str, Any], pressure: Any) -> str:
    """按新规则重算单条合格记录的下次校验日，返回写入的日期字符串。"""
    next_day = calc_next_date(record.get("校验日期"), pressure)
    record[NEXT_DATE_FIELD] = next_day.isoformat() if next_day else ""
    tier_name, _, _ = pressure_tier(pressure)
    record[TIER_FIELD] = tier_name or "未分档"
    return record[NEXT_DATE_FIELD]


def warn_window(next_day: date, today: date | None = None) -> tuple[bool, bool]:
    """返回 (是否已超期, 是否在 7 天预警窗口内)。"""
    today = today or date.today()
    delta = (next_day - today).days
    return delta < 0, 0 <= delta <= WARN_DAYS


def upcoming_deadline(today: date | None = None) -> date:
    today = today or date.today()
    return today + timedelta(days=WARN_DAYS)
