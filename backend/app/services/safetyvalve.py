"""安全阀校验业务规则：按整定压力档位与上次校验日期统一排期。

排期口径（判定规则集中在本模块，可通过 GET/PUT /api/safetyvalve/rules 调整）：
- 校验周期按整定压力档位确定：
  整定压力 < 1.6MPa            -> 12 个月
  1.6MPa <= 整定压力 < 10.0MPa -> 6 个月
  整定压力 >= 10.0MPa          -> 3 个月
- 下次校验日 = 上次（最近一次合格）校验日期 + 档位对应周期月数；
- 距下次校验日不足 7 天 -> 即将到期；已过下次校验日 -> 待校验（超期），列表前置并写明缘由；
- 超过使用年限（默认 10 年）的阀门另行提示，提示不改变排期状态；
- 已报废阀门不参与排期；同一只阀门重复登记合格只认第一次；
- 阀门台账与校验清单的状态/结论都由 :meth:`SafetyvalveService._derive` 一处算出，不允许两个口径。
"""
from __future__ import annotations

import calendar
from datetime import date, datetime
from typing import Any

from app.store import store

MODULE = "safetyvalve"

STATUS_PASS = "校验合格"
STATUS_SOON = "即将到期"
STATUS_PENDING = "待校验"
STATUS_SCRAPPED = "已报废"
STATUS_ORDER = [STATUS_PASS, STATUS_SOON, STATUS_PENDING, STATUS_SCRAPPED]

# 列表前置顺序：待校验（含超期、从未校验）-> 即将到期 -> 合格 -> 已报废
SORT_RANK = {STATUS_PENDING: 0, STATUS_SOON: 1, STATUS_PASS: 2, STATUS_SCRAPPED: 3}

# 默认整定压力档位：(档位名称, 下限MPa（None 表示不限下限）, 校验周期月数)
DEFAULT_TIERS: list[tuple[str, float | None, int]] = [
    ("低压档（整定压力<1.6MPa）", None, 12),
    ("中压档（1.6MPa≤整定压力<10.0MPa）", 1.6, 6),
    ("高压档（整定压力≥10.0MPa）", 10.0, 3),
]
DEFAULT_WARN_DAYS = 7
DEFAULT_LIFE_YEARS = 10

REQUIRED_FIELDS = ["安全阀编号", "所属设备", "公称通径", "整定压力"]
EDITABLE_FIELDS = ["安全阀编号", "所属设备", "公称通径", "整定压力", "出厂日期", "使用年限"]

ACTION_RULES = ["安排校验", "登记合格", "申请报废"]


def parse_date(value: Any) -> date | None:
    """宽容解析 YYYY-MM-DD；解析不了返回 None，由调用方决定如何提示。"""
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


def parse_pressure(value: Any) -> float | None:
    """解析整定压力（MPa），允许带 MPa/兆帕 等单位后缀。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower().replace("mpa", "").replace("兆帕", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def add_months(day: date, months: int) -> date:
    """按月数顺延，目标月份没有对应日（如 2 月 31 日）时落到月末。"""
    month_index = day.year * 12 + (day.month - 1) + months
    year, month = divmod(month_index, 12)
    month += 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last_day))


class SafetyvalveService:
    def __init__(self) -> None:
        # 判定规则可以在线调整；调整后调用 recalc_all 全量重算下次校验日。
        self.rules: dict[str, Any] = {
            "warn_days": DEFAULT_WARN_DAYS,
            "default_life_years": DEFAULT_LIFE_YEARS,
            "tiers": [
                {"name": name, "min_mpa": lower, "interval_months": months}
                for name, lower, months in DEFAULT_TIERS
            ],
        }
        self.recalc_all()

    # ---------- 规则与排期计算 ----------

    def _today(self) -> date:
        return date.today()

    def _tier_for(self, pressure: float) -> dict[str, Any]:
        """按整定压力从高到低匹配档位，返回档位名称与周期月数。"""
        tiers = sorted(self.rules["tiers"], key=lambda t: t["min_mpa"] or 0.0, reverse=True)
        for tier in tiers:
            lower = tier["min_mpa"]
            if lower is None or pressure + 1e-9 >= float(lower):
                return tier
        return tiers[-1]

    def _life_years(self, entry: dict[str, Any]) -> int:
        try:
            years = int(entry.get("使用年限"))
            return years if years > 0 else int(self.rules["default_life_years"])
        except (TypeError, ValueError):
            return int(self.rules["default_life_years"])

    def _latest_pass(self, entry: dict[str, Any]) -> dict[str, Any] | None:
        records = entry.get("校验记录") or []
        passes = [r for r in records if str(r.get("校验结论", "")).strip() == "合格"]
        if not passes:
            return None
        return max(passes, key=lambda r: parse_date(r.get("校验日期")) or date.min)

    def _next_due(self, last_date: date, pressure: float) -> tuple[date, dict[str, Any]]:
        tier = self._tier_for(pressure)
        return add_months(last_date, int(tier["interval_months"])), tier

    def _derive(self, entry: dict[str, Any], today: date) -> dict[str, Any]:
        """台账与清单共用的唯一判定口径，返回所有展示字段。"""
        pressure = parse_pressure(entry.get("整定压力"))
        derived: dict[str, Any] = {
            "整定压力": f"{pressure:g} MPa" if pressure is not None else entry.get("整定压力"),
            "压力档位": "",
            "校验周期": "",
            "校验日期": "",
            "下次校验日": "",
            "剩余天数": "",
            "校验结论": "未校验",
            "安全阀状态": STATUS_PENDING,
            "排期说明": "",
            "年限提示": "",
        }

        # 使用年限提示：无论是否报废都算出来，但提示本身不改状态。
        made_on = parse_date(entry.get("出厂日期"))
        life_end = add_months(made_on, self._life_years(entry) * 12) if made_on else None
        if life_end is not None and today > life_end:
            overdue_years = (today - life_end).days // 365
            tail = f"，已超期约 {overdue_years} 年" if overdue_years > 0 else "，已到限"
            derived["年限提示"] = f"出厂日期 {made_on:%Y-%m-%d}，{self._life_years(entry)} 年使用年限已满{tail}，建议评估报废或更新"

        if entry.get("status") == STATUS_SCRAPPED:
            last = self._latest_pass(entry)
            if last:
                derived["校验日期"] = last.get("校验日期", "")
                derived["校验结论"] = last.get("校验结论", "合格")
            derived["安全阀状态"] = STATUS_SCRAPPED
            derived["排期说明"] = "已报废，不参与校验排期"
            return derived

        last = self._latest_pass(entry)
        if last is None:
            derived["安全阀状态"] = STATUS_PENDING
            derived["排期说明"] = "从未登记合格校验记录，需尽快安排首次校验"
            return derived

        last_date = parse_date(last.get("校验日期"))
        if last_date is None:
            derived["安全阀状态"] = STATUS_PENDING
            derived["排期说明"] = f"上次校验日期「{last.get('校验日期')}」无法识别，需重新登记"
            return derived
        if pressure is None:
            derived["校验日期"] = f"{last_date:%Y-%m-%d}"
            derived["校验结论"] = "合格"
            derived["安全阀状态"] = STATUS_PENDING
            derived["排期说明"] = "整定压力缺失或无法解析，不能判定档位与下次校验日"
            return derived

        next_due, tier = self._next_due(last_date, pressure)
        days_left = (next_due - today).days
        derived["压力档位"] = tier["name"]
        derived["校验周期"] = f"{tier['interval_months']} 个月"
        derived["校验日期"] = f"{last_date:%Y-%m-%d}"
        derived["下次校验日"] = f"{next_due:%Y-%m-%d}"
        derived["剩余天数"] = days_left
        derived["校验结论"] = "合格"

        if days_left < 0:
            derived["安全阀状态"] = STATUS_PENDING
            derived["排期说明"] = f"校验已超期 {-days_left} 天，应于 {next_due:%Y-%m-%d} 前完成校验"
        elif days_left < int(self.rules["warn_days"]):
            derived["安全阀状态"] = STATUS_SOON
            if days_left == 0:
                derived["排期说明"] = f"下次校验日就是今天（{next_due:%Y-%m-%d}），请立即安排校验"
            else:
                derived["排期说明"] = f"距下次校验仅余 {days_left} 天（不足 {self.rules['warn_days']} 天），请尽快安排"
        else:
            derived["安全阀状态"] = STATUS_PASS
            derived["排期说明"] = f"校验合格，下次校验日 {next_due:%Y-%m-%d}，剩余 {days_left} 天"
        return derived

    def _refresh(self, entry: dict[str, Any], today: date | None = None) -> dict[str, Any]:
        """重算单条阀门：写回台账字段，并按新规则重算其历史校验记录的下次校验日。"""
        today = today or self._today()
        pressure = parse_pressure(entry.get("整定压力"))
        # 已有校验记录也按当前规则重算下次校验日（每次校验当时的整定压力为准）。
        for record in entry.get("校验记录") or []:
            record_pressure = parse_pressure(record.get("整定压力"))
            record_date = parse_date(record.get("校验日期"))
            if record_pressure is None:
                record_pressure = pressure
            if record_pressure is not None and record_date is not None:
                next_due, _ = self._next_due(record_date, record_pressure)
                record["下次校验日"] = f"{next_due:%Y-%m-%d}"

        derived = self._derive(entry, today)
        entry.update(derived)
        entry["status"] = derived["安全阀状态"]
        entry["pending"] = derived["安全阀状态"] in (STATUS_PENDING, STATUS_SOON)
        entry["abnormal"] = derived["安全阀状态"] == STATUS_PENDING
        return entry

    def recalc_all(self) -> None:
        """判定规则调整后调用：全部阀门（含报废阀的结论字段）按新规则重算。"""
        today = self._today()
        for entry in store.rows(MODULE):
            self._refresh(entry, today)

    # ---------- 查询 ----------

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        today = self._today()
        rows = [self._refresh(row, today) for row in store.rows(MODULE)]
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("安全阀编号", ""))]
        if status:
            rows = [row for row in rows if row.get("安全阀状态") == status]
        # 待校验/即将到期前置：状态优先级，同档内按下次校验日升序，再按编号稳定排序。
        rows.sort(key=lambda row: (
            SORT_RANK.get(row.get("安全阀状态"), 9),
            row.get("下次校验日") or "0000-00-00",
            int(row.get("id", 0)),
        ))
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def stats(self) -> dict[str, int]:
        today = self._today()
        rows = [self._refresh(row, today) for row in store.rows(MODULE)]
        result = {STATUS_PASS: 0, STATUS_SOON: 0, STATUS_PENDING: 0, STATUS_SCRAPPED: 0, "超年限": 0}
        for row in rows:
            result[row["安全阀状态"]] = result.get(row["安全阀状态"], 0) + 1
            if row.get("年限提示"):
                result["超年限"] += 1
        return result

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return self._refresh(entry) if entry else None

    def get_rules(self) -> dict[str, Any]:
        return {
            "warn_days": self.rules["warn_days"],
            "default_life_years": self.rules["default_life_years"],
            "tiers": [dict(tier) for tier in self.rules["tiers"]],
        }

    def update_rules(self, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        tiers = payload.get("tiers")
        if not isinstance(tiers, list) or not tiers:
            return None, "档位规则 tiers 不能为空"
        normalized: list[dict[str, Any]] = []
        for raw in tiers:
            try:
                months = int(raw["interval_months"])
                lower = raw.get("min_mpa")
                lower_value = None if lower is None else float(lower)
                name = str(raw["name"]).strip()
            except (KeyError, TypeError, ValueError):
                return None, "每个档位需包含 name、min_mpa（可空）、interval_months"
            if months <= 0 or not name:
                return None, "档位名称不能为空、周期月数必须为正整数"
            normalized.append({"name": name, "min_mpa": lower_value, "interval_months": months})
        lowers = sorted(t["min_mpa"] or 0.0 for t in normalized)
        if len(set(lowers)) != len(lowers):
            return None, "档位下限不能重复"
        warn_days = payload.get("warn_days", self.rules["warn_days"])
        life_years = payload.get("default_life_years", self.rules["default_life_years"])
        try:
            warn_days = int(warn_days)
            life_years = int(life_years)
        except (TypeError, ValueError):
            return None, "预警天数与使用年限必须是整数"
        if warn_days < 0 or life_years <= 0:
            return None, "预警天数不能为负、使用年限必须为正"
        self.rules = {"warn_days": warn_days, "default_life_years": life_years, "tiers": normalized}
        self.recalc_all()
        return self.get_rules(), "判定规则已更新，全部阀门的下次校验日已按新规则重算"

    # ---------- 写入 ----------

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        if parse_pressure(values.get("整定压力")) is None:
            return None, ["整定压力需为数值（单位 MPa）"]
        made_on = values.get("出厂日期")
        if str(made_on or "").strip() and parse_date(made_on) is None:
            return None, ["出厂日期需为 YYYY-MM-DD 格式"]

        rows = store.rows(MODULE)
        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        for field in ["安全阀编号", "所属设备", "公称通径"]:
            entry[field] = str(values.get(field)).strip()
        entry["整定压力"] = parse_pressure(values.get("整定压力"))
        entry["出厂日期"] = str(made_on).strip() if made_on else ""
        try:
            life_years = int(values.get("使用年限") or 0)
            entry["使用年限"] = life_years if life_years > 0 else int(self.rules["default_life_years"])
        except (TypeError, ValueError):
            entry["使用年限"] = int(self.rules["default_life_years"])
        entry["校验记录"] = []
        entry["status"] = STATUS_PENDING

        # 登记时可顺带补录首条合格校验记录。
        check_date = str(values.get("校验日期") or "").strip()
        if check_date:
            parsed = parse_date(check_date)
            if parsed is None:
                return None, ["校验日期需为 YYYY-MM-DD 格式"]
            entry["校验记录"].append({
                "校验日期": f"{parsed:%Y-%m-%d}",
                "整定压力": entry["整定压力"],
                "校验结论": "合格",
            })
        rows.append(entry)
        return self._refresh(entry), []

    def update_entry(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"安全阀 {entry_id} 不存在或已归档"
        if "整定压力" in values and str(values.get("整定压力") or "").strip():
            pressure = parse_pressure(values.get("整定压力"))
            if pressure is None:
                return None, "整定压力需为数值（单位 MPa）"
            values["整定压力"] = pressure
        if "出厂日期" in values and str(values.get("出厂日期") or "").strip():
            if parse_date(values.get("出厂日期")) is None:
                return None, "出厂日期需为 YYYY-MM-DD 格式"
        if "使用年限" in values and str(values.get("使用年限") or "").strip():
            try:
                years = int(values.get("使用年限"))
                if years <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                return None, "使用年限需为正整数"
        for field in EDITABLE_FIELDS:
            if field in values and str(values.get(field) or "").strip():
                entry[field] = values[field]
        # 整定压力（档位）改过后，立即按上次校验日期重算下次校验日，不再沿用老日子。
        return self._refresh(entry), "阀门台账已更新，下次校验日已按当前整定压力档位重算"

    def run_action(self, entry_id: int, action: str, values: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str]:
        values = values or {}
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"安全阀 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于安全阀校验可执行范围"
        if entry.get("status") == STATUS_SCRAPPED:
            return None, "该阀门已报废，不参与校验排期"

        if action == "安排校验":
            return self._refresh(entry), f"安全阀 {entry.get('安全阀编号')} 已加入本期待校验安排"

        if action == "申请报废":
            entry["status"] = STATUS_SCRAPPED
            self._refresh(entry)
            return entry, f"安全阀 {entry.get('安全阀编号')} 已报废，后续不再排期"

        # 登记合格：同一只阀同一天重复登记只认第一次。
        pressure = parse_pressure(values.get("整定压力"))
        if pressure is None:
            pressure = parse_pressure(entry.get("整定压力"))
        if pressure is None:
            return None, "整定压力缺失或无法解析，不能判定档位与校验周期"
        check_date = parse_date(values.get("校验日期")) or self._today()
        date_text = f"{check_date:%Y-%m-%d}"
        for record in entry.get("校验记录") or []:
            if str(record.get("校验结论", "")).strip() == "合格" and str(record.get("校验日期")) == date_text:
                return None, f"该阀已于 {date_text} 登记过合格结论，重复登记只认第一次"

        entry["整定压力"] = pressure
        entry.setdefault("校验记录", []).append({
            "校验日期": date_text,
            "整定压力": pressure,
            "校验结论": "合格",
        })
        self._refresh(entry)
        return entry, (
            f"合格已登记（{date_text}，{entry.get('压力档位')}，周期 {entry.get('校验周期')}），"
            f"下次校验日 {entry.get('下次校验日')}"
        )
