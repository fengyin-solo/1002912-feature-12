"""安全阀校验业务：台账与合格校验记录分开存放，排期口径统一走 safetyvalve_rules。

- 台账表 MODULE：阀门主数据（含当前整定压力、投用日期、使用年限、报废标记）；
- 记录表 RECORDS_MODULE：每次"登记合格"一条；同阀同日重复登记只认第一次，
  后续重复登记直接拒收；
- 列表/明细导出的视图全部由 rules.derive_view 派生，台账与校验清单结论同源；
- 已报废阀门不参与排期，并在列表中沉底；
- 整定压力修改或判定规则调整后，调用 recalc 让存量记录按新规则重算下次校验日。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.services import safetyvalve_rules as rules
from app.store import store

MODULE = "safetyvalve"
RECORDS_MODULE = "safetyvalve_records"

REQUIRED_FIELDS = ["安全阀编号", "所属设备", "公称通径"]
# 允许通过登记/修改写入的台账字段
LEDGER_FIELDS = [
    "安全阀编号", "所属设备", "公称通径", "整定压力",
    "投用日期", "使用年限", "校验日期",
]

STATUS_ORDER = rules.VALID_STATUSES
ACTION_RULES = {"安排校验": "待校验", "申请报废": "已报废"}


class SafetyvalveService:
    # ---- 读 ----
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
        today: date | None = None,
    ) -> tuple[list[dict[str, Any]], int, dict[str, int]]:
        today = today or date.today()
        views = [self._view(entry, today=today) for entry in store.rows(MODULE)]
        if keyword:
            views = [row for row in views if keyword in str(row.get("安全阀编号", ""))]
        if status:
            views = [row for row in views if row.get("status") == status]
        views.sort(key=rules.sort_key)
        summary = self._summarize(views)
        total = len(views)
        start = max(page - 1, 0) * size
        return views[start:start + size], total, summary

    def get_entry(self, entry_id: int, *, today: date | None = None) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        return self._view(entry, today=today)

    def list_records(self, entry_id: int | None = None) -> list[dict[str, Any]]:
        rows = store.rows(RECORDS_MODULE)
        if entry_id is not None:
            rows = [row for row in rows if int(row.get("安全阀ID", 0)) == entry_id]
        return sorted(rows, key=lambda row: (str(row.get("安全阀编号")), str(row.get("校验日期"))))

    # ---- 写 ----
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        code = str(values.get("安全阀编号")).strip()
        if self._find_by_code(code) is not None:
            return None, [f"安全阀编号「{code}」已存在，不能重复登记"]
        rows = store.rows(MODULE)
        entry: dict[str, Any] = {"id": self._next_id(rows)}
        for field in LEDGER_FIELDS:
            if values.get(field) is not None:
                entry[field] = str(values.get(field)).strip()
        entry.setdefault("使用年限", str(rules.DEFAULT_SERVICE_YEARS))
        entry["status"] = rules.STATUS_PENDING
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return self._view(entry), []

    def update_entry(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"安全阀 {entry_id} 不存在或已归档"
        if entry.get("status") == rules.SCRAPPED:
            return None, "安全阀已报废，台账不再允许修改"
        pressure_changed = False
        for field in LEDGER_FIELDS:
            if field in values and values.get(field) is not None:
                if field == "整定压力" and str(values[field]).strip() != str(entry.get(field, "")):
                    pressure_changed = True
                entry[field] = str(values[field]).strip()
        if pressure_changed:
            # 整定压力换了档位，下次校验日按新档位重算，提醒不再守着老日子
            self._recalc_entry(entry)
        self._sync_flags(entry)
        return self._view(entry), "安全阀台账已更新"

    def run_action(self, entry_id: int, action: str, values: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"安全阀 {entry_id} 不存在或已归档"
        values = values or {}

        if action == "登记合格":
            return self._register_qualified(entry, values)
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于安全阀校验可执行范围"

        target = ACTION_RULES[action]
        if target == rules.SCRAPPED:
            # 已报废阀门不参与排期；重复报废保持幂等
            entry["status"] = rules.SCRAPPED
            self._sync_flags(entry)
            return self._view(entry), "安全阀已报废，不再参与校验排期"

        if entry.get("status") == rules.SCRAPPED:
            return None, "安全阀已报废，不能再安排校验"
        if target == rules.STATUS_PENDING:
            entry["pending"] = True
            self._sync_flags(entry)
            return self._view(entry), "安全阀已安排校验"

        return None, f"动作「{action}」未配置处理规则"

    def recalc(self, *, today: date | None = None) -> dict[str, Any]:
        """判定规则调整后，对全部在役阀门及其合格记录重算下次校验日与状态。"""
        today = today or date.today()
        updated = 0
        for entry in store.rows(MODULE):
            if entry.get("status") == rules.SCRAPPED:
                continue
            self._recalc_entry(entry)
            self._sync_flags(entry, today=today)
            updated += 1
        return {"updated": updated, "total": len(store.rows(MODULE))}

    # ---- 内部 ----
    def _register_qualified(
        self, entry: dict[str, Any], values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        if entry.get("status") == rules.SCRAPPED:
            return None, "安全阀已报废，不能再登记合格"
        check_date_raw = str(values.get("校验日期") or "").strip()
        check_day = rules.parse_date(check_date_raw)
        if check_day is None:
            return None, "登记合格必须给出有效的校验日期（YYYY-MM-DD）"
        pressure = values.get("整定压力") or entry.get("整定压力")
        if rules.pressure_tier(pressure)[0] is None:
            return None, "整定压力缺失或无法识别，无法确定校验周期，请先补登整定压力"

        records = store.rows(RECORDS_MODULE)
        entry_id = int(entry["id"])
        # 同一只阀门同一天重复登记合格，只认第一次
        duplicate = next(
            (row for row in records
             if int(row.get("安全阀ID", 0)) == entry_id
             and rules.parse_date(row.get("校验日期")) == check_day
             and str(row.get("校验结论")) == "合格"),
            None,
        )
        if duplicate is not None:
            return None, (
                f"安全阀 {entry.get('安全阀编号')} 在 {check_day.isoformat()} "
                "已登记过合格，同阀同日重复登记只认第一次"
            )

        tier_name, _, _ = rules.pressure_tier(pressure)
        next_day = rules.calc_next_date(check_day, pressure)
        record = {
            "id": self._next_id(records),
            "安全阀ID": entry_id,
            "安全阀编号": entry.get("安全阀编号"),
            "校验日期": check_day.isoformat(),
            "整定压力": str(pressure).strip(),
            "压力档位": tier_name,
            "下次校验日": next_day.isoformat() if next_day else "",
            "校验结论": "合格",
            "校验机构": str(values.get("校验机构") or "").strip(),
        }
        records.append(record)

        # 台账上的最近信息同步刷新（排期仍由规则引擎从合格记录派生）
        entry["整定压力"] = str(pressure).strip()
        entry["校验日期"] = check_day.isoformat()
        entry["下次校验日"] = record["下次校验日"]
        entry["校验结论"] = "合格"
        self._sync_flags(entry)
        view = self._view(entry)
        view["登记记录"] = record
        return view, (
            f"合格已登记：{tier_name}，周期 "
            f"{rules.pressure_tier(pressure)[1]} 个月，下次校验日 {record['下次校验日']}"
        )

    def _recalc_entry(self, entry: dict[str, Any]) -> None:
        pressure = entry.get("整定压力")
        for record in self._records_for(int(entry["id"])):
            rules.recalc_record(record, pressure)
        last = rules.latest_qualified(self._records_for(int(entry["id"])))
        if last is not None:
            entry["校验日期"] = last["校验日期"]
            entry["下次校验日"] = last["下次校验日"]
            entry["校验结论"] = last["校验结论"]
        else:
            next_day = rules.calc_next_date(entry.get("校验日期"), pressure)
            entry["下次校验日"] = next_day.isoformat() if next_day else ""

    def _records_for(self, entry_id: int) -> list[dict[str, Any]]:
        return [row for row in store.rows(RECORDS_MODULE) if int(row.get("安全阀ID", 0)) == entry_id]

    def _view(self, entry: dict[str, Any], *, today: date | None = None) -> dict[str, Any]:
        return rules.derive_view(entry, self._records_for(int(entry["id"])), today=today)

    def _sync_flags(self, entry: dict[str, Any], *, today: date | None = None) -> None:
        """把规则引擎的结论回写 pending/abnormal，供全局概览统计复用。"""
        view = self._view(entry, today=today)
        status = view["status"]
        entry["status"] = status
        entry["pending"] = status not in (rules.STATUS_QUALIFIED, rules.SCRAPPED)
        entry["abnormal"] = status == rules.STATUS_OVERDUE or bool(view.get("超年限"))

    def _summarize(self, views: list[dict[str, Any]]) -> dict[str, int]:
        return {
            "校验合格": sum(1 for row in views if row["status"] == rules.STATUS_QUALIFIED),
            "即将到期": sum(1 for row in views if row["status"] == rules.STATUS_DUE_SOON),
            "校验超期": sum(1 for row in views if row["status"] == rules.STATUS_OVERDUE),
            "待校验": sum(1 for row in views if row["status"] == rules.STATUS_PENDING),
            "已报废": sum(1 for row in views if row["status"] == rules.SCRAPPED),
            "超年限": sum(1 for row in views if row.get("超年限")),
            "在役总数": sum(1 for row in views if row["status"] != rules.SCRAPPED),
        }

    def _find_by_code(self, code: str) -> dict[str, Any] | None:
        return next((row for row in store.rows(MODULE) if row.get("安全阀编号") == code), None)

    @staticmethod
    def _next_id(rows: list[dict[str, Any]]) -> int:
        return max((int(row.get("id", 0)) for row in rows), default=0) + 1
