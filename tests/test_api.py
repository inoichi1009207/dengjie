import os
import tempfile
import unittest

os.environ["DENGJIE_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
os.environ["LLM_FORCE_STUB"] = "1"
os.environ["DENGJIE_TODAY"] = "2026-09-16"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(app)
        r = cls.c.post("/api/register", json={"username": "alice", "password": "pass1234"})
        assert r.status_code == 200, r.text

    def test_01_settings_and_goal_flow(self):
        r = self.c.put("/api/settings", json={"weekly_hours": 10, "semester_start": "2026-09-07"})
        self.assertEqual(r.status_code, 200); self.assertEqual(r.json()["weekly_hours"], 10)
        r = self.c.post("/api/goals/decompose", json={"title": "刷完线代前四章", "due": "2026-10-14"})
        tasks = r.json()["tasks"]; self.assertEqual(len(tasks), 4); self.assertEqual(r.json()["llm"], "stub")
        r = self.c.post("/api/goals", json={"title": "刷完线代前四章", "due": "2026-10-14", "tasks": tasks})
        self.assertEqual(len(r.json()["task_ids"]), 4)
        w = self.c.get("/api/week").json()
        self.assertEqual(w["week_no"], 2)
        # 桩:今天 09-16,due 10-14 → 四段截止 09-23/09-30/10-07/10-14,本周(09-14~09-20)无承诺
        self.assertEqual(w["gap"]["committed_hours"], 0.0)
        w2 = self.c.get("/api/week?date=2026-09-23").json()
        self.assertEqual(w2["days"][2]["tasks"][0]["title"].startswith("刷完线代"), True)

    def test_02_schedule_parse_and_ledger(self):
        text = "高等数学 周一 3-4节 1-16周 东上院101\n大学物理 周三 5-6节 2-16周(双) 东下院201\n乱七八糟的一行"
        r = self.c.post("/api/schedule/parse", json={"text": text}).json()
        self.assertEqual(len(r["slots"]), 2); self.assertEqual(r["slots"][1]["weeks"][:2], [2, 4])
        r = self.c.put("/api/schedule", json={"slots": r["slots"]}).json()
        self.assertEqual(r["count"], 2); self.assertEqual(r["weekly_hours"], 53.0)   # 保存课表即自动采用 56 − 课时
        w = self.c.get("/api/week").json()
        self.assertEqual(w["class_hours"], 3.0)                # 第 2 教学周两门课各 2 节
        self.assertEqual(w["gap"]["weekly_hours"], 53.0)
        m = self.c.get("/api/month?year=2026&month=9").json()
        self.assertEqual(m["days"][0]["date"], "2026-08-31"); self.assertEqual(m["days"][-1]["date"], "2026-10-04")
        wed = next(d for d in m["days"] if d["date"] == "2026-09-16")
        self.assertTrue(wed["is_today"]); self.assertEqual(wed["class_slots"], 2)
        self.assertEqual(m["week"]["gap"]["weekly_hours"], 53.0)

    def test_02b_semester_any_day_and_defaults(self):
        c = TestClient(app); c.post("/api/register", json={"username": "carol", "password": "pass1234"})
        me = c.get("/api/me").json()
        self.assertEqual(me["weekly_hours"], 56.0); self.assertEqual(me["semester_start"], "2026-09-13")
        self.assertEqual(c.get("/api/week").json()["week_no"], 1)      # 9.13 周日起算 → 9.14 那周是第 1 周
        self.assertEqual(c.put("/api/settings", json={"semester_start": "2026-09-13"}).status_code, 200)
        gid = self.c.get("/api/goals").json()[0]["id"]
        r = self.c.post("/api/tasks", json={"title": "手动加的", "est_hours": 1, "due": "2026-09-17", "goal_id": gid})
        self.assertEqual(r.status_code, 200)
        self.assertIn("手动加的", [t["title"] for t in self.c.get("/api/goals").json()[0]["tasks"]])
        self.assertEqual(self.c.post("/api/tasks", json={"title": "  ", "est_hours": 1}).status_code, 400)
        self.assertEqual(self.c.post("/api/mail/import").status_code, 400)  # 无账号
        st = self.c.get("/api/integrations/status").json(); self.assertEqual(st["schedule_slots"], 2)

    def test_03_today_review_and_tired(self):
        self.c.put("/api/settings", json={"weekly_hours": 14})
        a = self.c.post("/api/tasks", json={"title": "今天的事", "est_hours": 2, "due": "2026-09-16"}).json()["id"]
        b = self.c.post("/api/tasks", json={"title": "大活", "est_hours": 5, "due": "2026-09-19"}).json()["id"]
        self.c.patch(f"/api/tasks/{b}", json={"scheduled_date": "2026-09-16"})
        t = self.c.get("/api/today").json()
        self.assertEqual({x["id"] for x in t["tasks"]} & {a, b}, {a, b})
        p = self.c.post("/api/review/too_tired/preview").json()
        self.assertEqual(p["tomorrow_cap"], 1.2)               # 14/7*0.6
        self.assertTrue(p["sentence"]); self.assertIsNone(p["advice"])
        r = self.c.post("/api/review/too_tired/apply", json={"moves": p["moves"], "overflow": p["overflow"]}).json(); self.assertEqual(r["moved"], len(p["moves"]))
        self.assertEqual(self.c.get("/api/today").json()["tasks"], [])
        r = self.c.post(f"/api/review/{a}/done").json(); self.assertEqual(r["streak"], 1)
        r = self.c.post(f"/api/review/{b}/postpone").json(); self.assertTrue(r["scheduled_date"] > "2026-09-16")
        p = self.c.post("/api/review/too_tired/preview").json(); self.assertIsNotNone(p["advice"])

    def test_04_pending_confirm_and_sync_token(self):
        self.assertEqual(self.c.post("/api/canvas/import").status_code, 400)  # 无令牌
        r = self.c.post("/api/login", json={"username": "alice", "password": "pass1234"}).json()
        tok = r["upload_token"]
        body = {"schedule": [{"name": "线性代数", "day": 2, "slot_start": 1, "slot_end": 2, "weeks": [1, 2, 3]}]}
        r = self.c.post("/api/sync", json=body, headers={"Authorization": f"Bearer {tok}"}); self.assertEqual(r.json()["slots"], 1)
        r = self.c.post("/api/sync", json=body, headers={"Authorization": f"Bearer {tok}"}); self.assertEqual(r.status_code, 401)  # 单次消费
        self.assertEqual(self.c.post("/api/sync", json=body).status_code, 401)

    def test_05_groups(self):
        g = self.c.post("/api/groups", json={"name": "共读小组"}).json()
        c2 = TestClient(app); c2.post("/api/register", json={"username": "bob", "password": "pass1234"})
        self.assertEqual(c2.post("/api/groups/join", json={"code": "nope"}).status_code, 404)
        self.assertEqual(c2.post("/api/groups/join", json={"code": g["invite_code"]}).status_code, 200)
        tasks = self.c.post("/api/goals/decompose", json={"title": "共读线代", "due": "2026-10-14"}).json()["tasks"]
        self.c.post("/api/goals", json={"title": "共读线代", "due": "2026-10-14", "tasks": tasks, "group_id": g["id"]})
        d = c2.get(f"/api/groups/{g['id']}").json()
        self.assertEqual(len(d["members"]), 2); self.assertIsNone(d["invite_code"])  # 非组长看不到码
        tid = d["goals"][0]["tasks"][0]["id"]
        self.assertEqual(c2.post(f"/api/tasks/{tid}/claim").json()["user_id"], d["members"][1]["id"] if d["members"][1]["username"] == "bob" else d["members"][0]["id"])
        self.assertEqual(self.c.post(f"/api/tasks/{tid}/claim").status_code, 400)  # 已被认领
        d = self.c.get(f"/api/groups/{g['id']}").json(); self.assertEqual(d["goals"][0]["tasks"][0]["assignee"], "bob")

    def test_06_auth_required(self):
        self.assertEqual(TestClient(app).get("/api/today").status_code, 401)
        self.assertEqual(self.c.get("/").status_code, 200)


if __name__ == "__main__":
    unittest.main()
