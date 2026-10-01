"""安全阀校验规则的单元测试。

仅依赖标准库即可运行：python3 backend/tests/test_safetyvalve_rules.py
（也兼容 pytest 收集）
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import safetyvalve_rules as rules  # noqa: E402

TODAY = date(2026, 10, 1)


class PressureTierTests(unittest.TestCase):
    def test_tier_boundaries(self) -> None:
        cases = [
            ("12.5MPa", "高压档", 6),
            ("10.0", "高压档", 6),
            ("2.5 MPa", "中压档", 12),
            ("1.6MPa", "中压档", 12),
            ("0.8", "低压档", 24),
            ("abc", None, None),
            (None, None, None),
        ]
        for raw, name, months in cases:
            with self.subTest(raw=raw):
                got_name, got_months, _ = rules.pressure_tier(raw)
                self.assertEqual(got_name, name)
                self.assertEqual(got_months, months)


class NextDateTests(unittest.TestCase):
    def test_next_date_by_tier(self) -> None:
        self.assertEqual(
            rules.calc_next_date("2026-01-12", "12.5MPa"), date(2026, 7, 12)
        )
        self.assertEqual(
            rules.calc_next_date("2025-10-05", "1.6"), date(2026, 10, 5)
        )
        self.assertEqual(
            rules.calc_next_date("2026-10-01", "0.6MPa"), date(2028, 10, 1)
        )

    def test_month_end_clamping(self) -> None:
        # 1/31 + 6 个月仍是 7/31；2/29 加 24 个月后 2026 年 2 月只有 28 天
        self.assertEqual(rules.calc_next_date("2026-01-31", "12.5MPa"), date(2026, 7, 31))
        self.assertEqual(rules.calc_next_date("2024-02-29", "0.6MPa"), date(2026, 2, 28))

    def test_missing_inputs(self) -> None:
        self.assertIsNone(rules.calc_next_date(None, "2.5MPa"))
        self.assertIsNone(rules.calc_next_date("2026-01-01", None))


class DeriveViewTests(unittest.TestCase):
    def _entry(self, **overrides: object) -> dict[str, object]:
        entry: dict[str, object] = {
            "id": 1,
            "安全阀编号": "SV-T1",
            "整定压力": "2.5MPa",
            "投用日期": "2022-05-10",
            "使用年限": "15",
            "status": "校验合格",
        }
        entry.update(overrides)
        return entry

    def test_qualified(self) -> None:
        entry = self._entry()
        records = [{"校验日期": "2026-04-18", "校验结论": "合格"}]
        view = rules.derive_view(entry, records, today=TODAY)
        self.assertEqual(view["status"], "校验合格")
        self.assertEqual(view["下次校验日"], "2027-04-18")

    def test_due_soon_within_seven_days(self) -> None:
        entry = self._entry()
        records = [{"校验日期": "2025-10-05", "校验结论": "合格"}]
        view = rules.derive_view(entry, records, today=TODAY)
        self.assertEqual(view["status"], "即将到期")
        self.assertIn("不足 7 天", view["提醒缘由"])

    def test_overdue(self) -> None:
        entry = self._entry(**{"整定压力": "12.5MPa"})
        records = [{"校验日期": "2026-01-12", "校验结论": "合格"}]
        view = rules.derive_view(entry, records, today=TODAY)
        self.assertEqual(view["status"], "校验超期")
        self.assertIn("已超期 81 天", view["提醒缘由"])

    def test_pending_without_record(self) -> None:
        view = rules.derive_view(self._entry(), [], today=TODAY)
        self.assertEqual(view["status"], "待校验")
        self.assertIn("从未登记过合格校验", view["提醒缘由"])

    def test_scrapped_excluded_from_schedule(self) -> None:
        entry = self._entry(status="已报废")
        records = [{"校验日期": "2024-11-15", "校验结论": "合格"}]
        view = rules.derive_view(entry, records, today=TODAY)
        self.assertEqual(view["status"], "已报废")
        self.assertEqual(view["下次校验日"], "")
        self.assertIsNone(view["_days_left"])
        self.assertEqual(view["提醒缘由"], "")

    def test_service_life_hint_independent_of_status(self) -> None:
        entry = self._entry(**{"投用日期": "2009-09-30"})
        records = [{"校验日期": "2026-08-08", "校验结论": "合格"}]
        view = rules.derive_view(entry, records, today=TODAY)
        self.assertTrue(view["超年限"])
        self.assertIn("已超过使用年限", view["提醒缘由"])

    def test_latest_qualified_is_schedule_base(self) -> None:
        records = [
            {"校验日期": "2025-04-20", "校验结论": "合格"},
            {"校验日期": "2026-04-18", "校验结论": "合格"},
        ]
        last = rules.latest_qualified(records)
        self.assertIsNotNone(last)
        self.assertEqual(last["校验日期"], "2026-04-18")

    def test_sort_overdue_first_scrapped_last(self) -> None:
        def view(eid: int, status: str, days: int | None) -> dict[str, object]:
            return {"id": eid, "status": status, "_days_left": days,
                    "下次校验日": None, "安全阀编号": f"SV-{eid}"}
        rows = [
            view(1, "校验合格", 100),
            view(2, "已报废", None),
            view(3, "校验超期", -80),
            view(4, "即将到期", 3),
            view(5, "待校验", None),
        ]
        rows.sort(key=rules.sort_key)
        self.assertEqual([r["id"] for r in rows], [3, 4, 5, 1, 2])


if __name__ == "__main__":
    unittest.main(verbosity=2)
