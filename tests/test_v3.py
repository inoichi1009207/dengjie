import os
import tempfile
import unittest

os.environ["DENGJIE_DB"] = os.path.join(tempfile.mkdtemp(), "v3.db")
os.environ["LLM_FORCE_STUB"] = "1"
os.environ["DENGJIE_TODAY"] = "2026-09-16"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


class V3(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(app)
        assert cls.c.post("/api/register", json={"username": "dora", "password": "pass1234"}).status_code == 200

    def test_01_upload_txt_and_pdf_missing_dep(self):
        r = self.c.post("/api/schedule/upload", files={"file": ("kb.txt", "高等数学 周一 3-4节 1-16周 东上院101\n".encode(), "text/plain")})
        self.assertEqual(r.status_code, 200); self.assertEqual(len(r.json()["slots"]), 1)
        r = self.c.post("/api/schedule/upload", files={"file": ("kb.pdf", b"%PDF-1.4 fake", "application/pdf")})
        self.assertIn(r.status_code, (200, 501))   # 装了 pypdf 就 200(假 PDF 会 422/500 之外),没装就 501

    def test_02_bundle_import(self):
        text = '助手说:这是结果\n{"schedule":[{"name":"线代","day":2,"slot_start":1,"slot_end":2,"weeks":[1,2]}],' \
               '"canvas_tasks":[{"external_id":"canvas:assignment:1","title":"[线代] 作业1","due":"2026-09-20","est_hours":2}],' \
               '"mail_tasks":[{"external_id":"mail:9","title":"提交实验报告","due":"2026-09-25","est_hours":1}]}\n完'
        r = self.c.post("/api/import/bundle", json={"text": text}).json()
        self.assertEqual((r["schedule"], r["canvas_tasks"], r["mail_tasks"]), (1, 1, 1)); self.assertEqual(r["weekly_hours"], 40.5)
        r = self.c.post("/api/import/bundle", json={"text": text}).json(); self.assertEqual(r["skipped"], 2)
        pend = self.c.get("/api/tasks?status=pending").json(); self.assertEqual({t["source"] for t in pend}, {"canvas", "email"})
        self.assertEqual(self.c.post("/api/import/bundle", json={"text": "没有json"}).status_code, 400)
        self.assertIn("schedule", self.c.get("/api/prompt").text)

    def test_03_discuss(self):
        r = self.c.post("/api/goals/discuss", json={"title": "刷完线代", "due": "2026-10-14", "history": [], "feedback": "太多了,合并一下"}).json()
        self.assertEqual(len(r["tasks"]), 2); self.assertTrue(r["note"])
        self.assertEqual(self.c.post("/api/goals/discuss", json={"title": "x", "feedback": " "}).status_code, 400)

    def test_04_settle_and_ddl(self):
        tasks = self.c.post("/api/goals/decompose", json={"title": "读完一本书", "due": "2026-09-16"}).json()["tasks"]
        gid = self.c.post("/api/goals", json={"title": "读完一本书", "due": "2026-09-16", "tasks": tasks}).json()["goal_id"]
        gid2 = self.c.post("/api/goals", json={"title": "以后的事", "due": "2026-10-30", "tasks": tasks}).json()["goal_id"]
        d = self.c.get("/api/ddl").json()
        self.assertEqual([g["id"] for g in d["due_goals"]], [gid]); self.assertEqual(d["due_goals"][0]["days_left"], 0)
        self.assertIn(gid2, [g["id"] for g in d["upcoming_goals"]])
        r = self.c.post(f"/api/goals/{gid}/settle", json={"action": "extend", "new_due": "2026-09-30"}).json(); self.assertEqual(r["due"], "2026-09-30")
        self.assertEqual(self.c.get("/api/ddl").json()["due_goals"], [])
        r = self.c.post(f"/api/goals/{gid}/settle", json={"action": "achieved"}).json(); self.assertEqual(r["status"], "achieved")
        g = next(x for x in self.c.get("/api/goals").json() if x["id"] == gid)
        self.assertTrue(all(t["status"] == "done" for t in g["tasks"]))
        self.assertEqual(self.c.post(f"/api/goals/{gid2}/settle", json={"action": "drop"}).json()["status"], "dropped")
        self.assertEqual(self.c.post(f"/api/goals/{gid2}/settle", json={"action": "nope"}).status_code, 400)

    def test_06_demo_seed_reset_and_delete_goal(self):
        r = self.c.post("/api/demo/seed").json(); self.assertTrue(r["ok"]); self.assertLess(r["weekly_hours"], 42)
        st = self.c.get("/api/integrations/status").json(); self.assertEqual(st["schedule_slots"], 10)
        t = self.c.get("/api/today").json(); self.assertGreaterEqual(len(t["pending"]), 4); self.assertTrue(t["classes"])  # 09-16 周三有课
        gid = r["goal_id"]
        self.assertEqual(self.c.delete(f"/api/goals/{gid}").status_code, 200)
        m = self.c.get("/api/month?year=2026&month=10").json()
        self.assertFalse(any(x["goal_id"] == gid for d in m["days"] for x in d["tasks"]))   # 删目标后日历同步消失
        self.assertEqual(self.c.delete(f"/api/goals/{gid}").status_code, 404)
        self.c.post("/api/demo/reset"); self.assertEqual(self.c.get("/api/goals").json(), []); self.assertEqual(self.c.get("/api/schedule").json(), [])

    def test_05_group_tasks_and_rate(self):
        g = self.c.post("/api/groups", json={"name": "共读"}).json()
        c2 = TestClient(app); c2.post("/api/register", json={"username": "eve", "password": "pass1234"}); c2.post("/api/groups/join", json={"code": g["invite_code"]})
        gg = self.c.post("/api/goals", json={"title": "共读线代", "due": "2026-10-01", "tasks": [], "group_id": g["id"]}).json()["goal_id"]
        tid = self.c.post("/api/tasks", json={"title": "第一章", "est_hours": 3, "goal_id": gg}).json()["id"]     # 共同目标下手动加任务
        d = c2.get(f"/api/groups/{g['id']}").json()
        self.assertEqual(d["goals"][0]["tasks"][0]["assignee"], None)
        c2.post(f"/api/tasks/{tid}/claim"); c2.post(f"/api/review/{tid}/done")
        d = self.c.get(f"/api/groups/{g['id']}").json()
        eve = next(m for m in d["members"] if m["username"] == "eve"); dora = next(m for m in d["members"] if m["username"] == "dora")
        self.assertEqual((eve["claimed"], eve["done"], eve["completion_rate"], eve["hours_done"]), (1, 1, 1.0, 3.0))
        self.assertEqual((dora["claimed"], dora["completion_rate"]), (0, 0.0))   # 个人任务不计入
        self.assertEqual(d["goals"][0]["progress"], 1.0)
        self.assertEqual(self.c.post("/api/tasks", json={"title": "x", "goal_id": 99999}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
