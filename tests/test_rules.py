import datetime as dt
import unittest

from app import rules


def T(id, status="confirmed", rem=2.0, due=None, sd=None):
    return {"id": id, "title": f"t{id}", "status": status, "remaining_hours": rem, "due": due, "scheduled_date": sd}


class Rules(unittest.TestCase):
    today = dt.date(2026, 9, 16)  # 周三

    def test_week_bounds(self):
        mon, sun = rules.week_bounds(self.today)
        self.assertEqual((mon.isoformat(), sun.isoformat()), ("2026-09-14", "2026-09-20"))

    def test_week_number(self):
        self.assertEqual(rules.week_number(dt.date(2026, 9, 7), self.today), 2)
        self.assertIsNone(rules.week_number(None, self.today))
        self.assertIsNone(rules.week_number(dt.date(2026, 10, 5), self.today))

    def test_class_hours_and_suggestion(self):
        slots = [{"slot_start": 1, "slot_end": 2, "weeks": [1, 2]}, {"slot_start": 3, "slot_end": 4, "weeks": [5]}]
        self.assertEqual(rules.class_hours_for_week(slots, 2), 1.5)
        self.assertEqual(rules.suggested_weekly_hours(1.5), 68.5)

    def test_capacity_gap(self):
        tasks = [T(1, due="2026-09-18"), T(2, sd="2026-09-20", rem=5), T(3, due="2026-09-30"),
                 T(4, status="done", due="2026-09-16"), T(5, due="2026-09-10", rem=1)]  # 5 逾期
        g = rules.capacity_gap(tasks, 6.0, self.today)
        self.assertEqual(g["committed_hours"], 8.0)
        self.assertEqual(g["gap"], 2.0)
        self.assertEqual(sorted(g["task_ids"]), [1, 2, 5])

    def test_today_tasks(self):
        tasks = [T(1, sd="2026-09-16"), T(2, due="2026-09-16"), T(3, sd="2026-09-17"), T(4, due="2026-09-10"), T(5, status="pending", sd="2026-09-16")]
        self.assertEqual([t["id"] for t in rules.today_tasks(tasks, self.today)], [4, 2, 1])

    def test_too_tired_plan(self):
        tasks = [T(1, sd="2026-09-16", rem=2, due="2026-09-18"), T(2, sd="2026-09-16", rem=3, due="2026-09-25"),
                 T(3, sd="2026-09-17", rem=1, due="2026-09-17"), T(4, sd="2026-09-16", rem=6, due="2026-09-19")]
        p = rules.too_tired_plan(tasks, self.today, daily_hours=5.0)
        self.assertEqual(p["tomorrow_cap"], 3.0)
        by_id = {m["id"]: m["to"] for m in p["moves"]}
        self.assertEqual(by_id[3], "2026-09-17")            # 截止最近且最小,进明天
        self.assertEqual(by_id[1], "2026-09-17")            # 1+2 = 3 ≤ cap
        self.assertNotEqual(by_id[4], "2026-09-17")         # 6h 放不进明天,后推
        self.assertTrue(all(m["to"] <= "2026-09-20" for m in p["moves"]))
        # 溢出:再塞 20h 的任务本周放不下
        p2 = rules.too_tired_plan(tasks + [T(9, sd="2026-09-16", rem=20)], self.today, 5.0)
        self.assertEqual([o["id"] for o in p2["overflow"]], [9])

    def test_streak(self):
        self.assertEqual(rules.streak(["2026-09-15", "2026-09-14", "2026-09-12"], self.today), 2)
        self.assertEqual(rules.streak(["2026-09-16", "2026-09-15"], self.today), 2)
        self.assertEqual(rules.streak([], self.today), 0)


if __name__ == "__main__":
    unittest.main()
