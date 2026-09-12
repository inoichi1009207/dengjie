import os
import tempfile
import unittest

os.environ["DENGJIE_DB"] = os.path.join(tempfile.mkdtemp(), "v3.db")
os.environ["LLM_FORCE_STUB"] = "1"
os.environ["DENGJIE_TODAY"] = "2026-09-16"
os.environ["DENGJIE_NO_NET"] = "1"

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
        self.assertEqual((r["schedule"], r["canvas_tasks"], r["mail_tasks"]), (1, 1, 1)); self.assertEqual(r["weekly_hours"], 68.5)
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
        r = self.c.post("/api/demo/seed").json(); self.assertTrue(r["ok"]); self.assertLess(r["weekly_hours"], 70)
        st = self.c.get("/api/integrations/status").json(); self.assertEqual(st["schedule_slots"], 12)
        t = self.c.get("/api/today").json(); self.assertGreaterEqual(len(t["pending"]), 4); self.assertTrue(t["classes"])  # 09-16 周三有课
        gid = r["goal_id"]
        self.assertEqual(self.c.delete(f"/api/goals/{gid}").status_code, 200)
        m = self.c.get("/api/month?year=2026&month=10").json()
        self.assertFalse(any(x["goal_id"] == gid for d in m["days"] for x in d["tasks"]))   # 删目标后日历同步消失
        self.assertEqual(self.c.delete(f"/api/goals/{gid}").status_code, 404)
        self.c.post("/api/demo/reset"); self.assertEqual(self.c.get("/api/goals").json(), []); self.assertEqual(self.c.get("/api/schedule").json(), [])

    def test_07_sample_timeline_postpone(self):
        r = self.c.post("/api/import/sample").json(); self.assertEqual(r["schedule"], 12); self.assertEqual(r["canvas_tasks"] + r["mail_tasks"], 6)
        names = {s["name"] for s in self.c.get("/api/sample-bundle").json()["schedule"]}; self.assertIn("高等数学(1)", names); self.assertIn("综合实践", names)
        a = self.c.post("/api/tasks", json={"title": "今天的大活", "est_hours": 5, "due": "2026-09-16", "scheduled_date": "2026-09-16"}).json()["id"]
        t = self.c.get("/api/today").json()
        kinds = [b["kind"] for b in t["timeline"]]; self.assertIn("class", kinds); self.assertIn("task", kinds)
        cls = [b for b in t["timeline"] if b["kind"] == "class"]; self.assertEqual(cls[0]["start"], 14.917)   # 09-16 第 1 周:大学物理(双周)不上,思修 7-8 节 14:55 起
        task = next(b for b in t["timeline"] if b["id"] == a); self.assertIsNotNone(task["start"]); self.assertLessEqual(task["end"] - task["start"], 3.0)
        for b in t["timeline"]:
            for c in t["timeline"]:
                if b is not c and b["start"] is not None and c["start"] is not None:
                    self.assertTrue(b["end"] <= c["start"] + 1e-9 or c["end"] <= b["start"] + 1e-9, "时间轴块重叠")
        tl = self.c.get("/api/timeline?date=2026-09-17").json(); self.assertEqual(tl["date"], "2026-09-17")
        r = self.c.post(f"/api/review/{a}/postpone").json(); self.assertEqual(r["postponed"], 1); self.assertTrue(r["past_due"])
        self.assertEqual(self.c.get("/api/today").json()["tasks"], [x for x in self.c.get("/api/today").json()["tasks"] if x["id"] != a])
        r = self.c.post("/api/schedule/upload", files={"file": ("kb.png", b"\x89PNG\r\n\x1a\n", "image/png")}); self.assertEqual(r.status_code, 501)
        r = self.c.post("/api/schedule/upload", files={"file": ("kb.md", "| 节次 | 星期一 |\n|---|---|\n| 第 3-4 节 | 高等数学 周一 3-4节 1-16周 |\n".encode(), "text/markdown")}); self.assertEqual(r.status_code, 200)

    def test_09_plan_auto(self):
        from app import rules
        import datetime as dt
        T = lambda i, h, due: {"id": i, "title": f"t{i}", "status": "confirmed", "remaining_hours": h, "due": due}
        p = rules.plan_days([T(1, 4, "2026-09-18"), T(2, 2, "2026-09-17"), T(3, 9, "2026-09-30"), T(4, 5, "2026-09-16")], dt.date(2026, 9, 16), 4.0, 14, {"2026-09-16": 1.5})
        self.assertEqual(p["assign"][4], "2026-09-16")                # 截止最近先排,当天容量 4−1.5=2.5 → 拆块
        self.assertTrue(any(o["id"] == 4 for o in p["overflow"]))      # 5h 在截止日前放不完
        self.assertEqual(p["assign"][2], "2026-09-17")
        self.assertTrue(p["assign"][3] >= "2026-09-18")
        self.assertTrue(all(v <= 4.0 + 1e-9 for v in p["load"].values()))
        self.c.post("/api/tasks", json={"title": "排程用", "est_hours": 2, "due": "2026-09-20"})
        r = self.c.post("/api/plan/auto").json(); self.assertGreaterEqual(r["planned"], 1)
        from app.llm import merge_adjacent
        m = merge_adjacent([{"name": "实验", "day": 5, "slot_start": 5, "slot_end": 6, "weeks": [3], "location": None, "teacher": None},
                            {"name": "实验", "day": 5, "slot_start": 7, "slot_end": 8, "weeks": [3], "location": "中心", "teacher": None}])
        self.assertEqual((len(m), m[0]["slot_start"], m[0]["slot_end"], m[0]["location"]), (1, 5, 8, "中心"))

    def test_10_capacity_and_batch(self):
        me = self.c.get("/api/me").json(); h0 = me["weekly_hours"]
        r = self.c.post("/api/settings/boost_weekly").json(); self.assertAlmostEqual(r["weekly_hours"], round(h0 * 1.1, 1))
        a = self.c.post("/api/tasks", json={"title": "明天的", "est_hours": 2, "due": "2026-09-17", "scheduled_date": "2026-09-17"}).json()["id"]
        r = self.c.post("/api/review/pull_tomorrow").json(); self.assertEqual(r["id"], a)
        self.assertIn(a, [t["id"] for t in self.c.get("/api/today").json()["tasks"]])
        r = self.c.patch("/api/tasks_batch", json={"items": [{"id": a, "remaining_hours": 0.5}, {"id": a, "remaining_hours": None}]}).json(); self.assertEqual(r["updated"], 1)
        t = next(x for x in self.c.get("/api/tasks").json() if x["id"] == a); self.assertEqual(t["remaining_hours"], 0.5); self.assertEqual(t["est_hours"], 2.0)
        self.assertEqual(self.c.patch("/api/tasks_batch", json={"items": [{"id": 99999, "remaining_hours": 1}]}).status_code, 404)

    def test_11_group_free_tasks_and_edit(self):
        g = self.c.post("/api/groups", json={"name": "自习小组"}).json()
        tid = self.c.post("/api/tasks", json={"title": "订自习室", "est_hours": 0.5, "group_id": g["id"]}).json()["id"]
        d = self.c.get(f"/api/groups/{g['id']}").json()
        self.assertEqual([x["id"] for x in d["group_tasks"]], [tid]); self.assertIsNone(d["group_tasks"][0]["assignee"])
        self.assertEqual(self.c.post(f"/api/tasks/{tid}/claim").status_code, 200)
        r = self.c.patch(f"/api/tasks/{tid}", json={"title": "订周三自习室", "est_hours": 1.5, "due": "2026-09-18"}).json()
        self.assertEqual((r["title"], r["est_hours"], r["due"]), ("订周三自习室", 1.5, "2026-09-18"))
        self.assertEqual(self.c.post("/api/tasks", json={"title": "x", "group_id": 99999}).status_code, 403)

    def test_12_grades_remind_password_catalog_rank(self):
        r = self.c.post("/api/import/sample").json(); self.assertEqual(r.get("grades"), 5)
        g = self.c.get("/api/grades").json(); self.assertEqual(g["count"], 5); self.assertTrue(3.0 < g["gpa"] < 4.3); self.assertEqual(g["weakest"][0]["course"], "大学物理")
        self.assertEqual(self.c.post("/api/grades", json={"grades": [{"course": "大学物理", "credit": 4, "score": 80, "term": "2025-2026-1"}]}).json()["imported"], 1)
        self.assertEqual(self.c.get("/api/grades").json()["count"], 5)   # 同课同学期覆盖不重复
        self.assertEqual(self.c.post("/api/remind/send_now").status_code, 400)   # 无邮箱账号
        self.assertEqual(self.c.put("/api/remind", json={"hour": 21}).json()["remind_hour"], 21)
        self.assertEqual(self.c.put("/api/remind", json={"hour": 25}).status_code, 400)
        self.assertEqual(self.c.get("/api/me").json()["remind_hour"], 21)
        self.assertEqual(self.c.post("/api/account/password", json={"old_password": "wrong", "new_password": "newpass1"}).status_code, 401)
        self.assertEqual(self.c.post("/api/account/password", json={"old_password": "pass1234", "new_password": "newpass1"}).status_code, 200)
        c2 = TestClient(app); self.assertEqual(c2.post("/api/login", json={"username": "dora", "password": "newpass1"}).status_code, 200)
        self.assertEqual(c2.post("/api/login", json={"username": "dora", "password": "pass1234"}).status_code, 401)
        from app import llm
        cat = llm.catalog_match("期中前刷完 MIT 18.01 前四单元"); self.assertTrue(any("18.01" in c["title"] for c in cat))
        tagged = llm.tag_resources([{"title": "a", "resource": "MIT 18.01SC", "est_hours": 1, "due": None}, {"title": "b", "resource": "某网盘链接", "est_hours": 1, "due": None}, {"title": "c", "resource": None}], cat)
        self.assertTrue(tagged[0]["resource_verified"]); self.assertIn("ocw.mit.edu", tagged[0]["resource_url"])
        self.assertFalse(tagged[1]["resource_verified"]); self.assertIn("未核验", tagged[1]["resource"]); self.assertIsNone(tagged[2]["resource_verified"])
        gr = self.c.post("/api/groups", json={"name": "排行组"}).json(); d = self.c.get(f"/api/groups/{gr['id']}").json()
        self.assertEqual(d["members"][0]["rank"], 1)

    def test_08_keywords(self):
        from app import llm
        self.assertEqual(llm._keywords("共读《Analysis I》前四章"), ["Analysis I"])
        self.assertIn("MIT 18.01", llm._keywords("期中前刷完 MIT 18.01 前四单元"))
        self.assertEqual(llm._keywords("把绩点稳住"), [])

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


class V10(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(app)
        assert cls.c.post("/api/register", json={"username": "fay", "password": "pass1234"}).status_code == 200

    def test_clamp_3h_and_done_keeps_hours(self):
        a = self.c.post("/api/tasks", json={"title": "大活", "est_hours": 8, "due": "2026-09-16", "scheduled_date": "2026-09-16"}).json()["id"]
        t = next(x for x in self.c.get("/api/tasks").json() if x["id"] == a); self.assertEqual((t["est_hours"], t["remaining_hours"]), (3.0, 3.0))
        self.assertEqual(self.c.patch(f"/api/tasks/{a}", json={"est_hours": 9}).json()["est_hours"], 3.0)
        self.assertEqual(self.c.patch("/api/tasks_batch", json={"items": [{"id": a, "remaining_hours": 7}]}).json()["updated"], 1)
        self.assertEqual(next(x for x in self.c.get("/api/tasks").json() if x["id"] == a)["remaining_hours"], 3.0)
        before = self.c.get("/api/week").json()["gap"]["committed_hours"]
        self.c.post(f"/api/review/{a}/done")
        after = self.c.get("/api/week").json()["gap"]
        self.assertEqual(after["committed_hours"], before)           # 做完的时间不会加回空余
        self.assertEqual(after["done_hours"], 3.0)
        t = self.c.get("/api/today").json(); self.assertEqual(t["daily_cap"], 10.0); self.assertEqual([x["id"] for x in t["done_today"]], [a])

    def test_group_member_can_edit_any_group_task(self):
        g = self.c.post("/api/groups", json={"name": "改任务组"}).json()
        c2 = TestClient(app); c2.post("/api/register", json={"username": "gus", "password": "pass1234"}); c2.post("/api/groups/join", json={"code": g["invite_code"]})
        gg = self.c.post("/api/goals", json={"title": "共同目标", "due": "2026-10-01", "tasks": [{"title": "t1", "est_hours": 1}], "group_id": g["id"]}).json()
        tid = gg["task_ids"][0]
        c2.post(f"/api/tasks/{tid}/claim")                                                   # gus 认领
        r = self.c.patch(f"/api/tasks/{tid}", json={"title": "t1 改过"}); self.assertEqual(r.status_code, 200)   # fay 未认领也能改
        r = c2.patch(f"/api/tasks/{tid}", json={"est_hours": 2}); self.assertEqual(r.json()["est_hours"], 2.0)
        c3 = TestClient(app); c3.post("/api/register", json={"username": "hal", "password": "pass1234"})
        self.assertEqual(c3.patch(f"/api/tasks/{tid}", json={"title": "x"}).status_code, 404)  # 非成员不能改


class V11(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(app)
        assert cls.c.post("/api/register", json={"username": "ivy", "password": "pass1234"}).status_code == 200

    def test_tired_advice_and_daily_review(self):
        a = self.c.post("/api/tasks", json={"title": "今天的事", "est_hours": 2, "due": "2026-09-16", "scheduled_date": "2026-09-16"}).json()["id"]
        self.assertEqual(self.c.post("/api/review/too_tired/advice", json={"reason": " "}).status_code, 400)
        r = self.c.post("/api/review/too_tired/advice", json={"reason": "实验报告写到两点", "history": []}).json(); self.assertTrue(r["advice"])
        self.assertEqual(next(x for x in self.c.get("/api/tasks").json() if x["id"] == a)["scheduled_date"], "2026-09-16")   # 只给建议,没动安排
        self.c.post(f"/api/review/{a}/done")
        r = self.c.post("/api/review/daily", json={"note": "今天效率还行", "mood": "good"}).json()
        self.assertEqual((r["stats"]["done"], r["stats"]["streak"]), (1, 1)); self.assertTrue(r["review"]["ai_comment"])
        r = self.c.get("/api/review/daily").json(); self.assertEqual(r["review"]["mood"], "good"); self.assertEqual(r["stats"]["done_hours"], 2.0)
        r = self.c.post("/api/review/daily", json={"note": "改一下", "mood": "ok"}).json(); self.assertEqual(r["review"]["note"], "改一下")   # 同日覆盖
