"""登阶 MVP 后端。跑法:uvicorn app.main:app --reload --port 8000"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import secrets

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import canvas as canvas_api
from . import db, llm, rules

app = FastAPI(title="登阶")
STATIC = os.path.join(os.path.dirname(__file__), "static")
db.init_db()

COOKIE = "dj_session"


def _demo_allowed(u) -> bool:
    """演示凭据只对白名单账号回落(DENGJIE_DEMO_USERS,逗号分隔;缺省 demo,12),别的注册用户拿不到团队邮箱/令牌。"""
    if not u:
        return False
    allowed = {x.strip() for x in os.environ.get("DENGJIE_DEMO_USERS", "demo,12").split(",") if x.strip()}
    return u.get("username") in allowed


def _demo_creds(u=None) -> dict:
    """演示账号:环境变量优先;其次 DENGJIE_DEMO_CREDS 指向的文件(四行:Canvas 令牌 / 空 / 邮箱账号 / 密码)。
    文件已 gitignore;值永不打印、永不进日志。只对白名单用户生效。"""
    if u is not None and not _demo_allowed(u):
        return {"canvas": "", "mail_user": "", "mail_pass": ""}
    out = {"canvas": os.environ.get("DEMO_CANVAS_TOKEN") or "", "mail_user": os.environ.get("DEMO_MAIL_USER") or "",
           "mail_pass": os.environ.get("DEMO_MAIL_PASS") or ""}
    p = os.environ.get("DENGJIE_DEMO_CREDS")
    if p and os.path.exists(p) and not all(out.values()):
        try:
            lines = [l.strip() for l in open(p, encoding="utf-8", errors="ignore").read().splitlines()]
            out["canvas"] = out["canvas"] or (lines[0] if len(lines) > 0 else "")
            out["mail_user"] = out["mail_user"] or (lines[2] if len(lines) > 2 else "")
            out["mail_pass"] = out["mail_pass"] or (lines[3] if len(lines) > 3 else "")
        except OSError:
            pass
    return out


def today() -> dt.date:
    return dt.date.fromisoformat(os.environ["DENGJIE_TODAY"]) if os.environ.get("DENGJIE_TODAY") else dt.date.today()


def _dtnow() -> dt.datetime:
    """演示/测试可用 DENGJIE_TODAY 固定日期,时间部分照真实时钟;所有过期计算都从这里出发。"""
    return dt.datetime.combine(today(), dt.datetime.now().time())


def now() -> str:
    return _dtnow().isoformat(timespec="seconds")


def _hash(pw: str, salt: str) -> str:
    return hashlib.scrypt(pw.encode(), salt=salt.encode(), n=2**14, r=8, p=1).hex()


# ── 鉴权 ────────────────────────────────────────────────────────────────

def current_user(request: Request) -> dict:
    tok = request.cookies.get(COOKIE)
    s = db.row("SELECT * FROM sessions WHERE token=? AND expires>?", (tok or "", now()))
    if not s:
        raise HTTPException(401, "未登录")
    u = db.row("SELECT * FROM users WHERE id=?", (s["user_id"],))
    if not u:
        raise HTTPException(401, "未登录")
    return u


def _issue_session(resp: Response, user_id: int) -> str:
    tok = secrets.token_urlsafe(32)
    exp = (_dtnow() + dt.timedelta(days=30)).isoformat(timespec="seconds")
    db.run("INSERT INTO sessions(token,user_id,expires) VALUES(?,?,?)", (tok, user_id, exp))
    resp.set_cookie(COOKIE, tok, httponly=True, samesite="lax", max_age=30 * 86400)
    return tok


def _issue_upload_token(user_id: int) -> str:
    tok = secrets.token_urlsafe(24)
    exp = (_dtnow() + dt.timedelta(minutes=10)).isoformat(timespec="seconds")
    db.run("INSERT INTO upload_tokens(token,user_id,expires) VALUES(?,?,?)", (tok, user_id, exp))
    return tok


class Cred(BaseModel):
    username: str
    password: str


@app.post("/api/register")
def register(c: Cred, resp: Response):
    if len(c.username) < 2 or len(c.password) < 4:
        raise HTTPException(400, "用户名至少 2 位,密码至少 4 位")
    if db.row("SELECT id FROM users WHERE username=?", (c.username,)):
        raise HTTPException(409, "用户名已存在")
    salt = secrets.token_hex(8)
    uid = db.run("INSERT INTO users(username,pw_hash,salt,weekly_hours,semester_start,created) VALUES(?,?,?,?,?,?)",
                 (c.username, _hash(c.password, salt), salt, rules.WEEKLY_BASE_HOURS,
                  _safe_date(os.environ.get("DEFAULT_SEMESTER_START", "2026-09-13")) or "2026-09-13", now()))
    _issue_session(resp, uid)
    return {"ok": True, "user_id": uid}


@app.post("/api/login")
def login(c: Cred, resp: Response):
    u = db.row("SELECT * FROM users WHERE username=?", (c.username,))
    if not u or _hash(c.password, u["salt"]) != u["pw_hash"]:
        raise HTTPException(401, "用户名或密码错误")
    _issue_session(resp, u["id"])
    return {"ok": True, "user_id": u["id"], "upload_token": _issue_upload_token(u["id"])}


@app.post("/api/logout")
def logout(request: Request, resp: Response):
    db.run("DELETE FROM sessions WHERE token=?", (request.cookies.get(COOKIE) or "",))
    resp.delete_cookie(COOKIE)
    return {"ok": True}


def _public_user(u: dict) -> dict:
    return {"id": u["id"], "username": u["username"], "weekly_hours": u["weekly_hours"],
            "semester_start": u["semester_start"],
            "has_canvas_token": bool(u["canvas_token"] or _demo_creds(u)["canvas"]),
            "has_mail": bool((u.get("mail_user") and u.get("mail_pass")) or (_demo_creds(u)["mail_user"] and _demo_creds(u)["mail_pass"])),
            "mail_user": u.get("mail_user") or _demo_creds(u)["mail_user"],
            "remind_hour": u.get("remind_hour"), "last_remind": u.get("last_remind"),
            "llm": "deepseek" if llm.available() else "stub"}


@app.get("/api/me")
def me(u: dict = Depends(current_user)):
    return _public_user(u)


class Settings(BaseModel):
    weekly_hours: float | None = None
    semester_start: str | None = None
    canvas_token: str | None = None
    mail_user: str | None = None
    mail_pass: str | None = None


@app.put("/api/settings")
def settings(s: Settings, u: dict = Depends(current_user)):
    if s.weekly_hours is not None:
        if not (0 <= s.weekly_hours <= 168):
            raise HTTPException(400, "本周可投入须在 0-168 小时")
        db.run("UPDATE users SET weekly_hours=? WHERE id=?", (float(s.weekly_hours), u["id"]))
    if s.semester_start is not None:
        dt.date.fromisoformat(s.semester_start)  # 任意日期都行,按所在周的周一起算
        db.run("UPDATE users SET semester_start=? WHERE id=?", (s.semester_start, u["id"]))
    if s.canvas_token is not None:
        db.run("UPDATE users SET canvas_token=? WHERE id=?", (s.canvas_token or None, u["id"]))
    if s.mail_user is not None:
        db.run("UPDATE users SET mail_user=? WHERE id=?", (s.mail_user.strip() or None, u["id"]))
    if s.mail_pass is not None:
        db.run("UPDATE users SET mail_pass=? WHERE id=?", (s.mail_pass or None, u["id"]))
    return _public_user(db.row("SELECT * FROM users WHERE id=?", (u["id"],)))


# ── 目标与任务 ───────────────────────────────────────────────────────────

from pydantic import field_validator


def _safe_date(v):
    try:
        return _valid_date(v)
    except ValueError:
        return None


def _valid_date(v):
    if v in (None, ""):
        return None
    try:
        return dt.date.fromisoformat(str(v)[:10]).isoformat()
    except ValueError:
        raise ValueError("日期格式须为 YYYY-MM-DD")


class GoalIn(BaseModel):
    title: str
    due: str | None = None
    _v_due = field_validator("due", mode="before")(classmethod(lambda cls, v: _valid_date(v)))


class TaskIn(BaseModel):
    title: str
    est_hours: float = 1.0
    due: str | None = None
    resource: str | None = None
    scheduled_date: str | None = None
    goal_id: int | None = None
    group_id: int | None = None
    _v_due = field_validator("due", mode="before")(classmethod(lambda cls, v: _valid_date(v)))
    _v_sd = field_validator("scheduled_date", mode="before")(classmethod(lambda cls, v: _valid_date(v)))


class GoalCreate(GoalIn):
    tasks: list[TaskIn]
    group_id: int | None = None


@app.post("/api/goals/decompose")
def decompose(g: GoalIn, u: dict = Depends(current_user)):
    ctx = [] if os.environ.get("DENGJIE_NO_NET") == "1" else llm.enrich(g.title)
    return {"tasks": llm.decompose_goal(g.title, g.due, today(), ctx), "context": ctx,
            "llm": "deepseek" if llm.available() else "stub"}


class DiscussIn(GoalIn):
    history: list[dict] = []   # [{role:"user"|"assistant", content:str}],由前端保存
    feedback: str


@app.post("/api/goals/discuss")
def discuss(g: DiscussIn, u: dict = Depends(current_user)):
    if not g.feedback.strip():
        raise HTTPException(400, "先写下你的意见")
    out = llm.discuss_goal(g.title, g.due, today(), g.history, g.feedback.strip())
    return {**out, "llm": "deepseek" if llm.available() else "stub"}


class SettleIn(BaseModel):
    action: str                 # achieved | extend | drop
    new_due: str | None = None


@app.post("/api/goals/{gid}/settle")
def settle_goal(gid: int, s: SettleIn, u: dict = Depends(current_user)):
    g = db.row("SELECT * FROM goals WHERE id=?", (gid,))
    if not g or (g["user_id"] != u["id"] and not (g["group_id"] and db.row("SELECT 1 FROM group_members WHERE group_id=? AND user_id=?", (g["group_id"], u["id"])))):
        raise HTTPException(404, "目标不存在")
    if g["group_id"] and not db.row("SELECT 1 FROM groups WHERE id=? AND owner_id=?", (g["group_id"], u["id"])):
        raise HTTPException(403, "共同目标的结算(达成/延期/放弃)只有组长能做")
    if s.action == "achieved":
        db.run("UPDATE goals SET status='achieved', settled_at=? WHERE id=?", (now(), gid))
        db.run("UPDATE tasks SET status='done', remaining_hours=0 WHERE goal_id=? AND status!='done'", (gid,))   # 不写 done_at:结算不算本周工时
    elif s.action == "extend":
        if not s.new_due:
            raise HTTPException(400, "延期要给新截止日")
        try:
            nd = dt.date.fromisoformat(s.new_due)
        except ValueError:
            raise HTTPException(400, "新截止日须为 YYYY-MM-DD")
        if g["due"] and nd <= rules.d(g["due"]):
            raise HTTPException(400, "新截止日要晚于原截止日")
        db.run("UPDATE goals SET due=? WHERE id=?", (s.new_due, gid))
        db.run("UPDATE tasks SET due=?, scheduled_date=CASE WHEN scheduled_date IS NOT NULL AND scheduled_date<? THEN ? ELSE scheduled_date END WHERE goal_id=? AND status='confirmed' AND due<?",
               (s.new_due, today().isoformat(), today().isoformat(), gid, s.new_due))
    elif s.action == "drop":
        db.run("UPDATE goals SET status='dropped', settled_at=? WHERE id=?", (now(), gid))
        db.run("DELETE FROM tasks WHERE goal_id=? AND status!='done'", (gid,))
    else:
        raise HTTPException(400, "action 只能是 achieved / extend / drop")
    return db.row("SELECT * FROM goals WHERE id=?", (gid,))


@app.delete("/api/goals/{gid}")
def delete_goal(gid: int, u: dict = Depends(current_user)):
    g = db.row("SELECT * FROM goals WHERE id=?", (gid,))
    if not g or (g["user_id"] != u["id"] and not (g["group_id"] and db.row("SELECT 1 FROM groups WHERE id=? AND owner_id=?", (g["group_id"], u["id"])))):
        raise HTTPException(404, "目标不存在")
    db.run("DELETE FROM tasks WHERE goal_id=?", (gid,))
    db.run("DELETE FROM goals WHERE id=?", (gid,))
    return {"ok": True}


# ── 演示 ────────────────────────────────────────────────────────────────

DEMO_SCHEDULE = """高等数学(1) 周一 3-4节 1-16周 东上院101 张老师
大学英语 周一 7-8节 1-16周 东中院205
线性代数 周二 1-2节 1-16周 东下院305
程序设计思想与方法 周二 5-6节 1-16周 东上院203
大学物理 周三 3-4节 2-16周(双) 东下院201
思想道德与法治 周三 7-8节 1-16周 东中院101
高等数学(1) 周四 3-4节 1-16周 东上院101 张老师
体育 周四 7-8节 1-16周 体育馆
线性代数 周五 3-4节 1-16周 东下院305
大学物理实验 周五 5-8节 3-14周(单) 物理实验中心
综合实践 周六 3-4节 1-16周 学生创新中心
综合实践 周日 5-6节 2-16周(双) 学生创新中心"""


SAMPLE_BUNDLE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tools", "sample-bundle.json")


@app.get("/api/sample-bundle")
def sample_bundle():
    with open(SAMPLE_BUNDLE_PATH, encoding="utf-8") as f:
        return json.load(f)


@app.post("/api/import/sample")
def import_sample(u: dict = Depends(current_user)):
    """演示用:直接导入 tools/sample-bundle.json(与一键提示词同格式)。"""
    with open(SAMPLE_BUNDLE_PATH, encoding="utf-8") as f:
        return import_bundle(BundleIn(text=f.read()), u)


# 交大作息:节次 → (开始, 结束),24 小时制小数
SLOT_TIMES = {1: (8.0, 8.75), 2: (8.917, 9.667), 3: (10.0, 10.75), 4: (10.917, 11.667), 5: (12.917, 13.667), 6: (13.833, 14.583),
              7: (14.917, 15.667), 8: (15.833, 16.583), 9: (16.75, 17.5), 10: (18.0, 18.75), 11: (18.917, 19.667), 12: (19.833, 20.583), 13: (20.75, 21.5)}


def build_timeline(classes: list[dict], tasks: list[dict], day_start: float = 8.0, day_end: float = 22.0) -> list[dict]:
    """把课按作息表放到时间轴,任务按(截止日近、剩余估时小)顺序填进空档;每块最多 3 小时。返回按开始时间排序的块。"""
    blocks = []
    for c in classes:
        a, b = c.get("slot_start") or 1, c.get("slot_end") or (c.get("slot_start") or 1)
        if a in SLOT_TIMES and b in SLOT_TIMES:
            blocks.append({"kind": "class", "start": SLOT_TIMES[a][0], "end": SLOT_TIMES[b][1], "title": c["name"],
                           "sub": c.get("location") or "", "id": c.get("id")})
    busy = sorted((x["start"], x["end"]) for x in blocks)
    cursor = day_start
    for t in sorted(tasks, key=lambda x: (x.get("due") or "9999", float(x.get("remaining_hours") or 0))):
        need = min(3.0, max(0.5, float(t.get("remaining_hours") or 1)))
        placed = False
        while cursor + need <= day_end + 1e-9:
            clash = next(((s, e) for s, e in busy if s < cursor + need and e > cursor), None)
            if clash:
                cursor = clash[1] + 0.25; continue
            blocks.append({"kind": "task", "start": cursor, "end": cursor + need, "title": t["title"], "sub": f"预计 {t.get('remaining_hours')}h", "id": t["id"],
                           "source": t.get("source"), "goal_id": t.get("goal_id")})
            busy.append((cursor, cursor + need)); busy.sort(); cursor += need + 0.25; placed = True; break
        if not placed:
            blocks.append({"kind": "task", "start": None, "end": None, "title": t["title"], "sub": "今天排不下", "id": t["id"],
                           "source": t.get("source"), "goal_id": t.get("goal_id")})
    blocks.sort(key=lambda b: (b["start"] is None, b["start"] or 0))
    return blocks


@app.post("/api/plan/auto")
def plan_auto(u: dict = Depends(current_user)):
    """一键排程:把已确认任务摊到接下来两周的各天(课时扣减后的日容量),写回 scheduled_date。"""
    t = today()
    slots = _slots(u["id"])
    cbd = {}
    for i in range(14):
        dd = t + dt.timedelta(days=i)
        wk = rules.week_number(_semester_start(u), dd)
        cls = [] if (_semester_start(u) and wk is None) else [s for s in slots if s["day"] == dd.weekday() + 1 and (wk is None or not s["weeks"] or wk in s["weeks"])]
        cbd[dd.isoformat()] = rules.class_hours_for_week(cls, None)
    plan = rules.plan_days(_my_tasks(u["id"]), t, rules.DAILY_CAP, 14, cbd)
    for tid, date in plan["assign"].items():
        db.run("UPDATE tasks SET scheduled_date=? WHERE id=? AND user_id=?", (date, tid, u["id"]))
    return {"planned": len(plan["assign"]), "overflow": plan["overflow"], "load": plan["load"]}


@app.get("/api/timeline")
def timeline(date: str | None = None, u: dict = Depends(current_user)):
    day = _qdate(date)
    wk = rules.week_number(_semester_start(u), day)
    classes = [] if (_semester_start(u) and wk is None) else [s for s in _slots(u["id"]) if s["day"] == day.weekday() + 1 and (wk is None or not s["weeks"] or wk in s["weeks"])]
    tasks = rules.today_tasks(_my_tasks(u["id"]), day) if day == today() else \
        [x for x in _my_tasks(u["id"]) if x["status"] == "confirmed" and (x["scheduled_date"] or x["due"]) == day.isoformat()]
    return {"date": day.isoformat(), "blocks": build_timeline(classes, tasks)}


@app.post("/api/demo/reset")
def demo_reset(u: dict = Depends(current_user)):
    """清空当前账号的个人数据(不动小组)。"""
    for sql in ("DELETE FROM tasks WHERE user_id=? AND group_id IS NULL", "DELETE FROM goals WHERE user_id=?",
                "DELETE FROM schedule_slots WHERE user_id=?", "DELETE FROM progress_events WHERE user_id=?",
                "DELETE FROM fatigue_events WHERE user_id=?", "DELETE FROM daily_reviews WHERE user_id=?", "DELETE FROM grades WHERE user_id=?"):
        db.run(sql, (u["id"],))
    db.run("UPDATE users SET weekly_hours=? WHERE id=?", (rules.WEEKLY_BASE_HOURS, u["id"]))
    return {"ok": True}


@app.post("/api/demo/seed")
def demo_seed(u: dict = Depends(current_user)):
    """按 PRD 顺序灌一套演示数据:课表 → 一个已拆解目标 → 几条待确认的作业与邮件事项。视频里可以从任一步接着演。"""
    demo_reset(u)
    t = today()
    slots = [SlotIn(**s) for s in llm.parse_schedule_regex(DEMO_SCHEDULE)]
    _replace_slots(u["id"], slots)
    hours = _adopt_suggested(u)
    due = (t + dt.timedelta(days=45)).isoformat()
    gid = db.run("INSERT INTO goals(user_id,title,due,created) VALUES(?,?,?,?)", (u["id"], "期中前刷完 MIT 18.01 单变量微积分前四单元", due, now()))
    plan = [("Unit 1 导数:看完 L1–L7 视频并做 Problem Set 1", 6, 5), ("Unit 1 复习:重做 PS1 错题,整理求导法则表", 3, 8),
            ("Unit 2 微分应用:L9–L13 视频 + Problem Set 2", 6, 14), ("Unit 2 练习:极值、作图、最优化各做 10 题", 4, 18),
            ("Unit 3 积分:L18–L23 视频 + Problem Set 3", 6, 25), ("Unit 3 练习:换元与定积分计算 15 题", 4, 30),
            ("Unit 4 积分技巧:L27–L31 视频 + Problem Set 4", 6, 38), ("总复习:限时做 Exam 1–2 真题并订正", 5, 44)]
    for title, h, off in plan:
        d = (t + dt.timedelta(days=off)).isoformat()
        _insert_task(u["id"], TaskIn(title=title, est_hours=h, due=d, resource="MIT 18.01SC"), "ai", gid)
    _insert_task(u["id"], TaskIn(title="整理本周高数笔记", est_hours=1.5, due=t.isoformat(), scheduled_date=t.isoformat()), "manual")
    _insert_task(u["id"], TaskIn(title="预习线代第 2 章", est_hours=1, due=(t + dt.timedelta(days=1)).isoformat(), scheduled_date=t.isoformat()), "manual")
    for title, h, off, ext in (("[高等数学(1)] 作业 2:极限与连续", 2, 4, "demo:canvas:1"), ("[程序设计] Lab 1 提交", 3, 6, "demo:canvas:2")):
        _insert_task(u["id"], TaskIn(title=title, est_hours=h, due=(t + dt.timedelta(days=off)).isoformat()), "canvas", status="pending", external_id=ext)
    for title, h, off, ext in (("提交青洋奖学金申请表及证明材料", 2, 6, "demo:mail:1"), ("完成 2026 秋季学期返校注册报到", 0.5, 1, "demo:mail:2")):
        _insert_task(u["id"], TaskIn(title=title, est_hours=h, due=(t + dt.timedelta(days=off)).isoformat(), resource="来自邮件"), "email", status="pending", external_id=ext)
    return {"ok": True, "weekly_hours": hours, "goal_id": gid}


@app.get("/api/ddl")
def ddl(u: dict = Depends(current_user)):
    """倒计时视图:未结算目标 + 未完成任务,按截止日排;到期/逾期目标单列等结算。"""
    t = today()
    goals = db.rows("SELECT g.* FROM goals g WHERE g.status='active' AND g.due IS NOT NULL AND (g.user_id=? OR g.group_id IN (SELECT group_id FROM group_members WHERE user_id=?)) ORDER BY g.due", (u["id"], u["id"]))
    for g in goals:
        ts = db.rows("SELECT status FROM tasks WHERE goal_id=?", (g["id"],))
        g["can_settle"] = (not g["group_id"]) or bool(db.row("SELECT 1 FROM groups WHERE id=? AND owner_id=?", (g["group_id"], u["id"])))
        g["days_left"] = (rules.d(g["due"]) - t).days
        g["tasks_total"] = len(ts); g["tasks_done"] = sum(1 for x in ts if x["status"] == "done")
    tasks = [x for x in _my_tasks(u["id"]) if x["status"] == "confirmed" and x["due"]]
    for x in tasks:
        x["days_left"] = (rules.d(x["due"]) - t).days
    tasks.sort(key=lambda x: x["due"])
    return {"today": t.isoformat(), "due_goals": [g for g in goals if g["days_left"] <= 0],
            "upcoming_goals": [g for g in goals if g["days_left"] > 0], "tasks": tasks[:50]}


MAX_TASK_HOURS = rules.MAX_TASK_HOURS


def _clamp_hours(h: float | None) -> float:
    try:
        return round(min(MAX_TASK_HOURS, max(0.0, float(h if h is not None else 1.0))), 2)
    except (TypeError, ValueError):
        return 1.0


def _insert_task(user_id: int | None, t: TaskIn, source: str, goal_id: int | None = None,
                 group_id: int | None = None, status: str = "confirmed", external_id: str | None = None) -> int:
    t.est_hours = _clamp_hours(t.est_hours)
    return db.run(
        "INSERT INTO tasks(user_id,goal_id,group_id,title,source,status,est_hours,remaining_hours,due,scheduled_date,external_id,note,created)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (user_id, goal_id, group_id, t.title, source, status, t.est_hours, t.est_hours, t.due,
         t.scheduled_date or t.due, external_id, t.resource, now()))


@app.post("/api/goals")
def create_goal(g: GoalCreate, u: dict = Depends(current_user)):
    if g.group_id and not db.row("SELECT 1 FROM group_members WHERE group_id=? AND user_id=?", (g.group_id, u["id"])):
        raise HTTPException(403, "不在该小组")
    gid = db.run("INSERT INTO goals(user_id,group_id,title,due,created) VALUES(?,?,?,?,?)",
                 (None if g.group_id else u["id"], g.group_id, g.title, g.due, now()))
    ids = [_insert_task(None if g.group_id else u["id"], t, "ai", gid, g.group_id) for t in g.tasks]
    return {"goal_id": gid, "task_ids": ids}


@app.get("/api/goals")
def list_goals(u: dict = Depends(current_user)):
    goals = db.rows("SELECT * FROM goals WHERE user_id=? ORDER BY (status!='active'), id DESC", (u["id"],))
    for g in goals:
        g["tasks"] = db.rows("SELECT * FROM tasks WHERE goal_id=? ORDER BY due, id", (g["id"],))
    return goals


def _my_tasks(uid: int) -> list[dict]:
    return db.rows("SELECT * FROM tasks WHERE user_id=? ORDER BY due, id", (uid,))


@app.get("/api/tasks")
def list_tasks(status: str | None = None, u: dict = Depends(current_user)):
    ts = _my_tasks(u["id"])
    return [t for t in ts if not status or t["status"] == status]


@app.post("/api/tasks")
def add_task(t: TaskIn, u: dict = Depends(current_user)):
    if not t.title.strip():
        raise HTTPException(400, "任务标题不能为空")
    if t.group_id and not t.goal_id:  # 小组独立任务:不挂目标,未认领
        if not db.row("SELECT 1 FROM group_members WHERE group_id=? AND user_id=?", (t.group_id, u["id"])):
            raise HTTPException(403, "不在该小组")
        return {"id": _insert_task(None, t, "manual", None, t.group_id)}
    if t.goal_id:
        g = db.row("SELECT * FROM goals WHERE id=?", (t.goal_id,))
        if not g:
            raise HTTPException(404, "目标不存在")
        if g["group_id"]:  # 共同目标下加任务:任务归小组,未认领
            if not db.row("SELECT 1 FROM group_members WHERE group_id=? AND user_id=?", (g["group_id"], u["id"])):
                raise HTTPException(403, "不在该小组")
            return {"id": _insert_task(None, t, "manual", t.goal_id, g["group_id"])}
        if g["user_id"] != u["id"]:
            raise HTTPException(404, "目标不存在")
    return {"id": _insert_task(u["id"], t, "manual", t.goal_id)}


class TaskPatch(BaseModel):
    title: str | None = None
    est_hours: float | None = None
    remaining_hours: float | None = None
    due: str | None = None
    scheduled_date: str | None = None
    status: str | None = None
    _v_due = field_validator("due", mode="before")(classmethod(lambda cls, v: _valid_date(v)))
    _v_sd = field_validator("scheduled_date", mode="before")(classmethod(lambda cls, v: _valid_date(v)))


def _own_task(tid: int, uid: int) -> dict:
    """自己的任务;或所在小组的任务(共同目标/小组任务,不论是否认领、认领给谁)。"""
    t = db.row("SELECT * FROM tasks WHERE id=?", (tid,))
    if not t:
        raise HTTPException(404, "任务不存在")
    if t["user_id"] == uid:
        return t
    if t["group_id"] and db.row("SELECT 1 FROM group_members WHERE group_id=? AND user_id=?", (t["group_id"], uid)):
        return t
    raise HTTPException(404, "任务不存在")


@app.patch("/api/tasks/{tid}")
def patch_task(tid: int, p: TaskPatch, u: dict = Depends(current_user)):
    _own_task(tid, u["id"])
    fields = p.model_dump(exclude_none=True)
    if "status" in fields:
        raise HTTPException(400, "状态不能直接改:确认走 /confirm,完成走 /review/{id}/done,忽略走 DELETE")
    for k, v in fields.items():
        if k in ("est_hours", "remaining_hours"):
            v = _clamp_hours(v)
        db.run(f"UPDATE tasks SET {k}=? WHERE id=?", (v, tid))
        if k == "est_hours":
            db.run("UPDATE tasks SET remaining_hours=MIN(remaining_hours,?) WHERE id=?", (v, tid))
    if "due" in fields:  # 计划日不能晚于截止日
        db.run("UPDATE tasks SET scheduled_date=? WHERE id=? AND scheduled_date IS NOT NULL AND scheduled_date>?", (fields["due"], tid, fields["due"]))
    return db.row("SELECT * FROM tasks WHERE id=?", (tid,))


@app.delete("/api/tasks/{tid}")
def delete_task(tid: int, u: dict = Depends(current_user)):
    t = _own_task(tid, u["id"])
    if t["status"] == "pending" and t["external_id"]:
        db.run("UPDATE tasks SET status='ignored' WHERE id=?", (tid,))   # 忽略:留痕,下次拉取不再复活
        return {"ok": True, "ignored": True}
    db.run("DELETE FROM tasks WHERE id=?", (tid,))
    return {"ok": True}


@app.post("/api/tasks/{tid}/confirm")
def confirm_task(tid: int, u: dict = Depends(current_user)):
    _own_task(tid, u["id"])
    db.run("UPDATE tasks SET status='confirmed' WHERE id=? AND status='pending'", (tid,))
    return db.row("SELECT * FROM tasks WHERE id=?", (tid,))


@app.post("/api/tasks/{tid}/claim")
def claim_task(tid: int, u: dict = Depends(current_user)):
    t = db.row("SELECT * FROM tasks WHERE id=?", (tid,))
    if not t or not t["group_id"]:
        raise HTTPException(400, "不是小组任务")
    if not db.row("SELECT 1 FROM group_members WHERE group_id=? AND user_id=?", (t["group_id"], u["id"])):
        raise HTTPException(403, "不在该小组")
    n = db.run("UPDATE tasks SET user_id=? WHERE id=? AND user_id IS NULL AND status!='done'", (u["id"], tid))
    if not n:
        raise HTTPException(409, "已被别人认领")
    return db.row("SELECT * FROM tasks WHERE id=?", (tid,))


# ── 课表 ────────────────────────────────────────────────────────────────

class SlotIn(BaseModel):
    name: str
    teacher: str | None = None
    location: str | None = None
    day: int
    slot_start: int | None = None
    slot_end: int | None = None
    weeks: list[int] = []


class ScheduleText(BaseModel):
    text: str


class ScheduleIn(BaseModel):
    slots: list[SlotIn]


@app.post("/api/schedule/parse")
def schedule_parse(s: ScheduleText, u: dict = Depends(current_user)):
    return {"slots": llm.parse_schedule_text(s.text), "llm": "deepseek" if llm.available() else "stub"}


def _pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader  # 可选依赖;没装时前端会先用 pdf.js 在浏览器里抽文本
    except ImportError:
        raise HTTPException(501, "服务器未安装 pypdf;请用浏览器端解析(页面会自动尝试),或 pip install pypdf")
    import io
    return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)


@app.post("/api/schedule/upload")
async def schedule_upload(file: UploadFile = File(...), u: dict = Depends(current_user)):
    """上传课表文件(PDF / TXT / CSV)→ 抽文本 → 同一条解析管线。"""
    data = await file.read()
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(413, "文件超过 5MB")
    name = (file.filename or "").lower()
    if name.endswith(".pdf") or data[:4] == b"%PDF":
        text = _pdf_text(data)
    elif name.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")) or data[:4] in (b"\x89PNG", b"\xff\xd8\xff\xe0", b"\xff\xd8\xff\xe1"):
        raise HTTPException(501, "图片请在浏览器端识别(页面会自动 OCR);服务器不做图片识别")
    else:
        text = data.decode("utf-8", "ignore")
    if not text.strip():
        raise HTTPException(422, "没抽出文字:可能是扫描版 PDF,请截图或复制文字")
    return {"text": text[:20000], "slots": llm.parse_schedule_text(text), "llm": "deepseek" if llm.available() else "stub"}


class BundleIn(BaseModel):
    text: str   # AI 助手返回的 JSON(允许夹杂说明文字,取第一个 {...})


@app.post("/api/import/bundle")
def import_bundle(b: BundleIn, u: dict = Depends(current_user)):
    """一键导入:{schedule, canvas_tasks, mail_tasks} 三部分各自可空;任务一律进待确认区。"""
    raw = b.text.strip()
    i, j = raw.find("{"), raw.rfind("}")
    if i < 0 or j <= i:
        raise HTTPException(400, "没找到 JSON")
    try:
        obj = json.loads(raw[i:j + 1])
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"JSON 不合法:{e.msg}")
    out = {"schedule": 0, "canvas_tasks": 0, "mail_tasks": 0, "skipped": 0}
    sched = obj.get("schedule") or []
    from pydantic import ValidationError
    try:   # 先把三段全部校验完,再落库
        slots = [SlotIn(**{k: s.get(k) for k in ("name", "teacher", "location", "day", "slot_start", "slot_end", "weeks") if s.get(k) is not None}) for s in sched if s.get("name") and s.get("day")]
        pre = []
        for key, source in (("canvas_tasks", "canvas"), ("mail_tasks", "email")):
            for it in obj.get(key) or []:
                title = str(it.get("title") or "").strip()
                if title:
                    pre.append((key, source, it, TaskIn(title=title[:120], est_hours=float(it.get("est_hours") or 2), due=it.get("due") or None)))
    except (ValidationError, ValueError, TypeError) as e:
        raise HTTPException(400, f"JSON 里有一条不合法:{str(e)[:120]}")
    if sched:
        out["schedule"] = _replace_slots(u["id"], slots)
        out["weekly_hours"] = _adopt_suggested(u)
    grades = obj.get("grades") or []
    if grades:
        out["grades"] = _import_grades(u["id"], grades)
    for key, source, it, tin in pre:
        ext = str(it.get("external_id") or f"{source}:{tin.title[:40]}")
        if db.row("SELECT 1 FROM tasks WHERE user_id=? AND external_id=?", (u["id"], ext)):
            out["skipped"] += 1; continue
        _insert_task(u["id"], tin, source, status="pending", external_id=ext)
        out[key] += 1
    return out


def _import_grades(uid: int, items: list[dict]) -> int:
    n = 0
    for g in items:
        course = str(g.get("course") or "").strip()
        if not course:
            continue
        try:
            score = float(g.get("score")) if g.get("score") not in (None, "") else None
            credit = float(g.get("credit")) if g.get("credit") not in (None, "") else None
        except (TypeError, ValueError):
            continue
        db.run("INSERT INTO grades(user_id,course,credit,score,term) VALUES(?,?,?,?,?) ON CONFLICT(user_id,course,term) DO UPDATE SET credit=excluded.credit, score=excluded.score",
               (uid, course[:80], credit, score, str(g.get("term") or "")[:20]))
        n += 1
    return n


def _gpa_point(score: float) -> float:
    """交大 4.3 制绩点换算(常用口径;以教务公布为准)。"""
    for lo, p in ((95, 4.3), (90, 4.0), (85, 3.7), (80, 3.3), (75, 3.0), (70, 2.7), (67, 2.3), (65, 2.0), (62, 1.7), (60, 1.0)):
        if score >= lo:
            return p
    return 0.0


@app.get("/api/grades")
def grades_view(u: dict = Depends(current_user)):
    rows = db.rows("SELECT course,credit,score,term FROM grades WHERE user_id=? ORDER BY term DESC, course", (u["id"],))
    scored = [r for r in rows if r["score"] is not None]
    wsum = sum((r["credit"] or 1) for r in scored)
    gpa = round(sum(_gpa_point(r["score"]) * (r["credit"] or 1) for r in scored) / wsum, 2) if wsum else None
    avg = round(sum(r["score"] for r in scored) / len(scored), 1) if scored else None
    weak = sorted(scored, key=lambda r: r["score"])[:3]
    return {"count": len(rows), "gpa": gpa, "avg": avg, "grades": rows, "weakest": weak}


class GradesIn(BaseModel):
    grades: list[dict]


@app.post("/api/grades")
def grades_import(g: GradesIn, u: dict = Depends(current_user)):
    return {"imported": _import_grades(u["id"], g.grades)}


# ── 每日提醒邮件 ────────────────────────────────────────────────────────

def _compose_reminder(u: dict) -> tuple[str, str]:
    t = today()
    tasks = rules.today_tasks(_my_tasks(u["id"]), t)
    gap = rules.capacity_gap(_my_tasks(u["id"]), u["weekly_hours"], t)
    lines = [f"登阶 · {t.isoformat()} 今日清单", ""]
    lines += [f"- {x['title']}(预计 {x['remaining_hours']}h{',截止 ' + x['due'] if x['due'] else ''})" for x in tasks] or ["- 今天没有安排"]
    lines += ["", f"本周已排 {gap['committed_hours']}h / 承载力 {gap['weekly_hours']}h;" + (f"排多了 {gap['gap']}h,记得取舍" if gap["gap"] > 0 else f"还有 {-gap['gap']}h 空余"),
              "", "打开登阶处理:完成 / 太累了 / 推迟。"]
    return f"[登阶] {t.isoformat()} 今日清单({len(tasks)} 件)", "\n".join(lines)


def _send_reminder(u: dict) -> dict:
    from . import mail as mail_api
    user = u.get("mail_user") or _demo_creds(u)["mail_user"]
    pw = u.get("mail_pass") or _demo_creds(u)["mail_pass"]
    if not (user and pw):
        raise HTTPException(400, "没有邮箱账号:在「接入」页填入")
    to = user if "@" in user else f"{user}@sjtu.edu.cn"
    subject, body = _compose_reminder(u)
    try:
        port = mail_api.send_mail(user, pw, to, subject, body)
    except Exception as e:
        raise HTTPException(502, f"发信失败:{type(e).__name__}")
    db.run("UPDATE users SET last_remind=? WHERE id=?", (now(), u["id"]))
    db.run("INSERT INTO progress_events(user_id,task_id,action,at) VALUES(?,?,?,?)", (u["id"], None, "remind_sent", now()))
    return {"ok": True, "to": to, "port": port, "sent_at": now(), "subject": subject}


@app.post("/api/remind/send_now")
def remind_send_now(u: dict = Depends(current_user)):
    return _send_reminder(u)


class RemindIn(BaseModel):
    hour: int | None = None   # 0-23;None = 关闭


@app.put("/api/remind")
def remind_set(r: RemindIn, u: dict = Depends(current_user)):
    if r.hour is not None and not (0 <= r.hour <= 23):
        raise HTTPException(400, "小时须在 0–23")
    db.run("UPDATE users SET remind_hour=? WHERE id=?", (r.hour, u["id"]))
    return {"remind_hour": r.hour}


def _remind_tick() -> int:
    """定时线程每分钟调一次:到点且今天没发过的用户,发一封。返回发送数。"""
    nowdt = _dtnow(); sent = 0
    for u in db.rows("SELECT * FROM users WHERE remind_hour IS NOT NULL"):
        if u["remind_hour"] != nowdt.hour or (u["last_remind"] or "")[:10] == nowdt.date().isoformat():
            continue
        db.run("UPDATE users SET last_remind=? WHERE id=?", (nowdt.isoformat(timespec="seconds"), u["id"]))   # 失败也记,当天不再重试
        try:
            _send_reminder(u); sent += 1
        except Exception as e:
            print("[remind] failed for user", u["id"], type(e).__name__)
    return sent


def _start_remind_thread():
    import threading, time as _time
    if os.environ.get("DENGJIE_NO_REMIND_THREAD") == "1":
        return
    def loop():
        while True:
            try:
                _remind_tick()
            except Exception as e:
                print("[remind] tick error", type(e).__name__)
            _time.sleep(60)
    threading.Thread(target=loop, daemon=True, name="dengjie-remind").start()


_start_remind_thread()


# ── 账号 ────────────────────────────────────────────────────────────────

class PasswordIn(BaseModel):
    old_password: str
    new_password: str


@app.post("/api/account/password")
def change_password(p: PasswordIn, request: Request, u: dict = Depends(current_user)):
    if _hash(p.old_password, u["salt"]) != u["pw_hash"]:
        raise HTTPException(401, "原密码不对")
    if len(p.new_password) < 4:
        raise HTTPException(400, "新密码至少 4 位")
    salt = secrets.token_hex(8)
    db.run("UPDATE users SET pw_hash=?, salt=? WHERE id=?", (_hash(p.new_password, salt), salt, u["id"]))
    db.run("DELETE FROM sessions WHERE user_id=? AND token!=?", (u["id"], request.cookies.get(COOKIE) or ""))  # 踢掉其他设备
    return {"ok": True}


@app.get("/api/prompt", response_class=PlainTextResponse)
def prompt_text():
    p = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tools", "PROMPT.md")
    with open(p, encoding="utf-8") as f:
        return f.read()


def _replace_slots(uid: int, slots: list[SlotIn]) -> int:
    db.run("DELETE FROM schedule_slots WHERE user_id=?", (uid,))
    for s in slots:
        db.run("INSERT INTO schedule_slots(user_id,name,teacher,location,day,slot_start,slot_end,weeks) VALUES(?,?,?,?,?,?,?,?)",
               (uid, s.name, s.teacher, s.location, s.day, s.slot_start, s.slot_end, json.dumps(s.weeks or list(range(1, 17)))))
    return len(slots)


def _class_hours_week(u: dict, slots: list[dict], wk: int | None) -> float:
    """本周课时。学期已设而教学周为空(开学前/学期外)→ 0,与页面「今天没课」同一口径。"""
    if u.get("semester_start") and wk is None:
        return 0.0
    return rules.class_hours_for_week(slots, wk)


def _adopt_suggested(u: dict) -> float:
    """按课表推算本周可投入时长(日承载力 × 7 − 课时)并写入。"""
    wk = rules.week_number(rules.d(u["semester_start"]), rules.week_bounds(today())[0])
    h = rules.suggested_weekly_hours(_class_hours_week(u, _slots(u["id"]), wk))
    db.run("UPDATE users SET weekly_hours=? WHERE id=?", (h, u["id"]))
    return h


@app.put("/api/schedule")
def schedule_put(s: ScheduleIn, u: dict = Depends(current_user)):
    n = _replace_slots(u["id"], s.slots)
    return {"count": n, "weekly_hours": _adopt_suggested(u)}


def _slots(uid: int) -> list[dict]:
    out = db.rows("SELECT * FROM schedule_slots WHERE user_id=? ORDER BY day, slot_start", (uid,))
    for s in out:
        s["weeks"] = json.loads(s["weeks"])
    return out


@app.get("/api/schedule")
def schedule_get(u: dict = Depends(current_user)):
    return _slots(u["id"])


class SyncIn(BaseModel):
    schedule: list[SlotIn] = []
    canvas_token: str | None = None
    year: str | None = None
    term: str | None = None


@app.post("/api/sync")
def sync(body: SyncIn, authorization: str = Header(default="")):
    tok = authorization.removeprefix("Bearer ").strip()
    ut = db.row("SELECT * FROM upload_tokens WHERE token=? AND expires>?", (tok, now()))
    if not ut:
        raise HTTPException(401, "上传令牌无效或过期")
    db.run("DELETE FROM upload_tokens WHERE token=?", (tok,))  # 单次消费
    n = _replace_slots(ut["user_id"], body.schedule) if body.schedule else 0
    if body.canvas_token:
        db.run("UPDATE users SET canvas_token=? WHERE id=?", (body.canvas_token, ut["user_id"]))
    hours = _adopt_suggested(db.row("SELECT * FROM users WHERE id=?", (ut["user_id"],))) if n else None
    return {"ok": True, "slots": n, "canvas_token": bool(body.canvas_token), "weekly_hours": hours}


# ── 周视图 / 今日 / 复盘 ────────────────────────────────────────────────

def _semester_start(u: dict) -> dt.date | None:
    return rules.d(u["semester_start"])


def _qdate(date: str | None) -> dt.date:
    try:
        return rules.d(date) or today()
    except ValueError:
        raise HTTPException(400, "date 须为 YYYY-MM-DD")


@app.get("/api/week")
def week(date: str | None = None, u: dict = Depends(current_user)):
    day = _qdate(date)
    mon, sun = rules.week_bounds(day)
    wk = rules.week_number(_semester_start(u), mon)
    slots = _slots(u["id"])
    tasks = _my_tasks(u["id"])
    days = []
    for i in range(7):
        dd = mon + dt.timedelta(days=i)
        days.append({"date": dd.isoformat(), "weekday": i + 1,
                     "slots": [] if (_semester_start(u) and wk is None) else [s for s in slots if s["day"] == i + 1 and (wk is None or not s["weeks"] or wk in s["weeks"])],
                     "tasks": [t for t in tasks if t["status"] != "done" and (t["scheduled_date"] == dd.isoformat() or (not t["scheduled_date"] and t["due"] == dd.isoformat()))]})
    class_hours = _class_hours_week(u, slots, wk)
    return {"monday": mon.isoformat(), "sunday": sun.isoformat(), "week_no": wk, "days": days,
            "class_hours": class_hours, "suggested_weekly_hours": rules.suggested_weekly_hours(class_hours),
            "gap": rules.capacity_gap(tasks, u["weekly_hours"], day),
            "overdue": [t for t in tasks if t["status"] == "confirmed" and t["due"] and rules.d(t["due"]) < mon]}


@app.post("/api/settings/adopt_suggested")
def adopt_suggested(u: dict = Depends(current_user)):
    return {"weekly_hours": _adopt_suggested(u)}


@app.get("/api/month")
def month(year: int | None = None, month: int | None = None, u: dict = Depends(current_user)):
    """月视图:从该月 1 号所在周的周一到月末所在周的周日,每天的课与任务;附本周时间账。"""
    t = today()
    y, m = year or t.year, month or t.month
    first = dt.date(y, m, 1)
    last = (first.replace(month=m % 12 + 1, year=y + (m == 12)) - dt.timedelta(days=1))
    start = first - dt.timedelta(days=first.weekday())
    end = last + dt.timedelta(days=6 - last.weekday())
    slots = _slots(u["id"])
    sem_set = _semester_start(u) is not None
    tasks = [x for x in _my_tasks(u["id"]) if x["status"] != "done"]
    days = []
    day = start
    while day <= end:
        wk = rules.week_number(_semester_start(u), day)
        ds = day.isoformat()
        cls = [] if (sem_set and wk is None) else [s for s in slots if s["day"] == day.weekday() + 1 and (wk is None or not s["weeks"] or wk in s["weeks"])]
        days.append({"date": ds, "in_month": day.month == m, "week_no": wk, "is_today": day == t,
                     "class_slots": sum((s["slot_end"] or 0) - (s["slot_start"] or 0) + 1 for s in cls if s["slot_start"] and s["slot_end"]),
                     "classes": cls,
                     "tasks": [x for x in tasks if (x["scheduled_date"] or x["due"]) == ds],
                     "overdue": [x for x in tasks if x["status"] == "confirmed" and x["due"] == ds and day < t]})
        day += dt.timedelta(days=1)
    wk_now = rules.week_number(_semester_start(u), rules.week_bounds(t)[0])
    class_hours = _class_hours_week(u, slots, wk_now)
    return {"year": y, "month": m, "days": days, "today": t.isoformat(),
            "week": {"monday": rules.week_bounds(t)[0].isoformat(), "sunday": rules.week_bounds(t)[1].isoformat(),
                     "week_no": wk_now, "class_hours": class_hours,
                     "suggested_weekly_hours": rules.suggested_weekly_hours(class_hours),
                     "gap": rules.capacity_gap(_my_tasks(u["id"]), u["weekly_hours"], t)}}


def _done_dates(uid: int) -> list[str]:
    return [r["at"][:10] for r in db.rows("SELECT at FROM progress_events WHERE user_id=? AND action='done'", (uid,))]


@app.get("/api/today")
def today_view(u: dict = Depends(current_user)):
    t = today()
    tasks = _my_tasks(u["id"])
    wk = rules.week_number(_semester_start(u), t)
    classes = [] if (_semester_start(u) and wk is None) else [s for s in _slots(u["id"]) if s["day"] == t.weekday() + 1 and (wk is None or not s["weeks"] or wk in s["weeks"])]
    todays = rules.today_tasks(tasks, t)
    pc = {r["task_id"]: r["c"] for r in db.rows("SELECT task_id, COUNT(*) c FROM progress_events WHERE user_id=? AND action='postpone' GROUP BY task_id", (u["id"],))}
    for x in todays:
        x["postponed"] = pc.get(x["id"], 0)
    return {"date": t.isoformat(), "tasks": todays, "streak": rules.streak(_done_dates(u["id"]), t),
            "pending": [x for x in tasks if x["status"] == "pending"], "classes": classes, "week_no": wk,
            "daily_cap": rules.DAILY_CAP, "done_today": [x for x in tasks if x["status"] == "done" and (x["done_at"] or "")[:10] == t.isoformat()],
            "goal_titles": {g["id"]: g["title"] for g in db.rows("SELECT id,title FROM goals WHERE user_id=? OR group_id IN (SELECT group_id FROM group_members WHERE user_id=?)", (u["id"], u["id"]))},
            "onboarding": {"has_goal": bool(db.row("SELECT 1 FROM goals WHERE user_id=?", (u["id"],))), "has_schedule": bool(db.row("SELECT 1 FROM schedule_slots WHERE user_id=?", (u["id"],)))},
            "timeline": build_timeline(classes, todays)}


@app.post("/api/review/{tid}/done")
def review_done(tid: int, u: dict = Depends(current_user)):
    t = _own_task(tid, u["id"])
    if t["group_id"]:
        owner = db.row("SELECT 1 FROM groups WHERE id=? AND owner_id=?", (t["group_id"], u["id"]))
        if t["user_id"] is None:
            raise HTTPException(400, "先认领再完成")
        if t["user_id"] != u["id"] and not owner:
            raise HTTPException(403, "只有认领人或组长能标记完成")
    if t["status"] == "done":
        return {"ok": True, "already": True, "streak": rules.streak(_done_dates(u["id"]), today())}
    db.run("UPDATE tasks SET status='done', remaining_hours=0, done_at=? WHERE id=? AND status!='done'", (now(), tid))
    db.run("INSERT INTO progress_events(user_id,task_id,action,at) VALUES(?,?,?,?)", (u["id"], tid, "done", now()))
    return {"ok": True, "streak": rules.streak(_done_dates(u["id"]), today())}


@app.post("/api/review/{tid}/postpone")
def review_postpone(tid: int, u: dict = Depends(current_user)):
    t = _own_task(tid, u["id"])
    base = rules.d(t["scheduled_date"]) or rules.d(t["due"]) or today()
    new = max(base, today()) + dt.timedelta(days=1)
    db.run("UPDATE tasks SET scheduled_date=? WHERE id=?", (new.isoformat(), tid))
    db.run("INSERT INTO progress_events(user_id,task_id,action,at) VALUES(?,?,?,?)", (u["id"], tid, "postpone", now()))
    warn = bool(t["due"]) and new > rules.d(t["due"])
    n = db.row("SELECT COUNT(*) c FROM progress_events WHERE task_id=? AND action='postpone'", (tid,))["c"]
    goal = db.row("SELECT title,due FROM goals WHERE id=?", (t["goal_id"],)) if t["goal_id"] else None
    goal_risk = bool(goal and goal["due"] and new > rules.d(goal["due"]))
    return {"ok": True, "scheduled_date": new.isoformat(), "past_due": warn, "postponed": n,
            "goal": goal["title"] if goal else None, "goal_due": goal["due"] if goal else None, "goal_risk": goal_risk}


@app.post("/api/review/too_tired/preview")
def too_tired_preview(u: dict = Depends(current_user)):
    t = today()
    plan = rules.too_tired_plan(_my_tasks(u["id"]), t, u["weekly_hours"] / 7)
    plan["sentence"] = llm.replan_sentence(plan)
    n = db.row("SELECT COUNT(DISTINCT date) c FROM fatigue_events WHERE user_id=? AND date>=? AND date<?", (u["id"], (t - dt.timedelta(days=2)).isoformat(), t.isoformat()))["c"]
    plan["advice"] = ("连续三天太累了,要不要把目标截止日往后挪一挪?" if n >= 2 else
                      "连着两天太累了,建议把本周承载力下调两成。" if n == 1 else None)
    plan["advice_kind"] = "extend_due" if n >= 2 else ("reduce" if n == 1 else None)
    return plan


class PlanIn(BaseModel):
    moves: list[dict]
    overflow: list[dict] = []


@app.post("/api/review/too_tired/apply")
def too_tired_apply(p: PlanIn, u: dict = Depends(current_user)):
    t = today()
    todays = rules.today_tasks(_my_tasks(u["id"]), t)
    if not db.row("SELECT 1 FROM fatigue_events WHERE user_id=? AND date=?", (u["id"], t.isoformat())):
        db.run("INSERT INTO fatigue_events(user_id,date,remaining) VALUES(?,?,?)",
               (u["id"], t.isoformat(), sum(float(x["remaining_hours"] or 0) for x in todays)))
    for m in p.moves:
        _own_task(int(m["id"]), u["id"])
        try:
            to = _valid_date(m.get("to"))
        except ValueError:
            to = None
        if not to:
            raise HTTPException(400, "moves.to 日期非法")
        db.run("UPDATE tasks SET scheduled_date=? WHERE id=?", (to, int(m["id"])))
    nxt = (rules.week_bounds(t)[1] + dt.timedelta(days=1)).isoformat()
    for o in p.overflow:  # 本周放不下的:有截止日的回到截止日那格;没有的放到下周一,仍可见
        x = _own_task(int(o["id"]), u["id"])
        db.run("UPDATE tasks SET scheduled_date=? WHERE id=?", (x["due"] if x["due"] else nxt, int(o["id"])))
    db.run("INSERT INTO progress_events(user_id,task_id,action,at) VALUES(?,?,?,?)", (u["id"], None, "too_tired", now()))
    return {"ok": True, "moved": len(p.moves)}


class TiredChatIn(BaseModel):
    reason: str
    history: list[dict] = []


@app.post("/api/review/too_tired/advice")
def too_tired_advice(b: TiredChatIn, u: dict = Depends(current_user)):
    """太累了 → 填原因 → AI 只给建议,不动任何安排(动安排仍要用户点「就这么办」)。"""
    if not b.reason.strip():
        raise HTTPException(400, "写一句原因")
    plan = rules.too_tired_plan(_my_tasks(u["id"]), today(), u["weekly_hours"] / 7)
    return {"advice": llm.tired_advice(b.reason.strip(), plan, b.history)}


class DailyReviewIn(BaseModel):
    note: str = ""
    mood: str | None = None   # good | ok | tired


def _daily_stats(u: dict, t: dt.date) -> dict:
    ds = t.isoformat()
    ev = db.rows("SELECT action, COUNT(*) c FROM progress_events WHERE user_id=? AND substr(at,1,10)=? GROUP BY action", (u["id"], ds))
    m = {r["action"]: r["c"] for r in ev}
    done_today = [x for x in _my_tasks(u["id"]) if x["status"] == "done" and (x["done_at"] or "")[:10] == ds]
    left = rules.today_tasks(_my_tasks(u["id"]), t)
    return {"date": ds, "done": len(done_today), "done_hours": round(sum(float(x["est_hours"] or 0) for x in done_today), 1),
            "postponed": m.get("postpone", 0), "too_tired": 1 if db.row("SELECT 1 FROM fatigue_events WHERE user_id=? AND date=?", (u["id"], ds)) else 0,
            "left": len(left), "streak": rules.streak(_done_dates(u["id"]), t)}


@app.get("/api/review/daily")
def daily_review_get(date: str | None = None, u: dict = Depends(current_user)):
    t = _qdate(date)
    row = db.row("SELECT note,mood,ai_comment,created FROM daily_reviews WHERE user_id=? AND date=?", (u["id"], t.isoformat()))
    return {"stats": _daily_stats(u, t), "review": row}


@app.post("/api/review/daily")
def daily_review_post(b: DailyReviewIn, u: dict = Depends(current_user)):
    t = today(); stats = _daily_stats(u, t)
    comment = llm.review_comment(stats, b.note.strip())
    db.run("INSERT INTO daily_reviews(user_id,date,note,mood,ai_comment,created) VALUES(?,?,?,?,?,?) ON CONFLICT(user_id,date) DO UPDATE SET note=excluded.note, mood=excluded.mood, ai_comment=excluded.ai_comment",
           (u["id"], t.isoformat(), b.note.strip(), b.mood, comment, now()))
    return {"stats": stats, "review": {"note": b.note.strip(), "mood": b.mood, "ai_comment": comment}}


@app.post("/api/settings/reduce_weekly")
def reduce_weekly(u: dict = Depends(current_user)):
    """PRD 规则 4:连续太累 → 本周可投入下调两成。"""
    h = round(max(7.0, u["weekly_hours"] * 0.8), 1)
    db.run("UPDATE users SET weekly_hours=? WHERE id=?", (h, u["id"]))
    return {"weekly_hours": h}


@app.post("/api/settings/boost_weekly")
def boost_weekly(u: dict = Depends(current_user)):
    """规则 4 的反向:今天全做完还有余力 → 本周可投入上调一成(承载力上修)。"""
    h = round(min(168.0, u["weekly_hours"] * 1.1), 1)
    db.run("UPDATE users SET weekly_hours=? WHERE id=?", (h, u["id"]))
    db.run("INSERT INTO progress_events(user_id,task_id,action,at) VALUES(?,?,?,?)", (u["id"], None, "spare", now()))
    return {"weekly_hours": h}


@app.post("/api/review/pull_tomorrow")
def pull_tomorrow(u: dict = Depends(current_user)):
    """余力:把明天最早截止的一件拉到今天。"""
    t = today(); tm = (t + dt.timedelta(days=1)).isoformat()
    cand = [x for x in _my_tasks(u["id"]) if x["status"] == "confirmed" and (x["scheduled_date"] or x["due"]) == tm]
    if not cand:
        raise HTTPException(404, "明天没有可以提前的任务")
    cand.sort(key=lambda x: (x["due"] or "9999", float(x["remaining_hours"] or 0)))
    db.run("UPDATE tasks SET scheduled_date=? WHERE id=?", (t.isoformat(), cand[0]["id"]))
    return {"pulled": cand[0]["title"], "id": cand[0]["id"]}


class BatchEst(BaseModel):
    items: list[dict]   # [{id, remaining_hours, est_hours?}]


@app.patch("/api/tasks_batch")   # 不能放在 /api/tasks/{tid} 下面,会被当成 tid
def tasks_batch(b: BatchEst, u: dict = Depends(current_user)):
    """批量校准估时(PRD:复盘时用户修正剩余估时)。"""
    n = 0
    for it in b.items:
        t = _own_task(int(it["id"]), u["id"])
        rem = it.get("remaining_hours")
        if rem is None:
            continue
        rem = _clamp_hours(rem)
        est = _clamp_hours(it.get("est_hours") or max(float(t["est_hours"] or 0), rem))
        db.run("UPDATE tasks SET remaining_hours=?, est_hours=? WHERE id=?", (rem, max(est, rem), int(it["id"])))
        n += 1
    return {"updated": n}


# ── Canvas ──────────────────────────────────────────────────────────────

@app.post("/api/canvas/import")
def canvas_import(u: dict = Depends(current_user)):
    token = u["canvas_token"] or _demo_creds(u)["canvas"]
    if not token:
        raise HTTPException(400, "没有 Canvas 令牌:在设置里填入,或由操作员配置演示令牌")
    try:
        items = canvas_api.fetch_pending(token)
    except Exception as e:
        raise HTTPException(502, f"Canvas 拉取失败:{e}")
    added = 0
    for it in items:
        if db.row("SELECT 1 FROM tasks WHERE user_id=? AND external_id=?", (u["id"], it["external_id"])):
            continue
        title = f"[{it['course']}] {it['title']}" if it.get("course") else it["title"]
        _insert_task(u["id"], TaskIn(title=title, est_hours=it["est_hours"], due=it["due"]), "canvas",
                     status="pending", external_id=it["external_id"])
        added += 1
    return {"fetched": len(items), "added": added}


# ── 邮箱 / 接入状态 ─────────────────────────────────────────────────────

@app.post("/api/mail/import")
def mail_import(u: dict = Depends(current_user)):
    from . import mail as mail_api
    user = u.get("mail_user") or _demo_creds(u)["mail_user"]
    pw = u.get("mail_pass") or _demo_creds(u)["mail_pass"]
    if not (user and pw):
        raise HTTPException(400, "没有邮箱账号:在「接入」页填入(演示账号)")
    try:
        raw = mail_api.fetch_recent(user, pw, days=7, limit=30)
    except Exception as e:
        raise HTTPException(502, f"邮箱连接失败:{type(e).__name__}")  # 不回显原文,避免带出账号
    cleaned = [mail_api.clean(m) for m in raw]
    found = mail_api.extract_tasks(cleaned, today())
    subjects = {c["uid"]: c["subject"] for c in cleaned}
    added = 0
    for it in found:
        ext = f"mail:{it['uid'] or it['title'][:40]}"
        if db.row("SELECT 1 FROM tasks WHERE user_id=? AND external_id=?", (u["id"], ext)):
            continue
        _insert_task(u["id"], TaskIn(title=it["title"], est_hours=it["est_hours"], due=it["due"],
                                    resource=f"来自邮件:{subjects.get(it['uid'], '')[:60]}"), "email",
                     status="pending", external_id=ext)
        added += 1
    return {"fetched": len(raw), "found": len(found), "added": added}


@app.get("/api/integrations/status")
def integrations_status(u: dict = Depends(current_user)):
    cnt = lambda src: db.row("SELECT COUNT(*) c FROM tasks WHERE user_id=? AND source=?", (u["id"], src))["c"]
    return {"schedule_slots": len(_slots(u["id"])), "canvas": {"configured": bool(u["canvas_token"] or _demo_creds(u)["canvas"]), "tasks": cnt("canvas")},
            "mail": {"configured": bool((u.get("mail_user") and u.get("mail_pass")) or (_demo_creds(u)["mail_user"] and _demo_creds(u)["mail_pass"])),
                     "user": u.get("mail_user") or _demo_creds(u)["mail_user"], "tasks": cnt("email")},
            "llm": "deepseek" if llm.available() else "stub"}


# ── 小组 ────────────────────────────────────────────────────────────────

class GroupIn(BaseModel):
    name: str


class JoinIn(BaseModel):
    code: str


@app.post("/api/groups")
def create_group(g: GroupIn, u: dict = Depends(current_user)):
    code = secrets.token_hex(4)
    exp = (_dtnow() + dt.timedelta(hours=24)).isoformat(timespec="seconds")
    gid = db.run("INSERT INTO groups(name,invite_code,owner_id,invite_expires,created) VALUES(?,?,?,?,?)",
                 (g.name, code, u["id"], exp, now()))
    db.run("INSERT INTO group_members(group_id,user_id,joined) VALUES(?,?,?)", (gid, u["id"], now()))
    return {"id": gid, "invite_code": code, "invite_expires": exp}


@app.post("/api/groups/join")
def join_group(j: JoinIn, u: dict = Depends(current_user)):
    g = db.row("SELECT * FROM groups WHERE invite_code=? AND invite_expires>?", (j.code.strip(), now()))
    if not g:
        raise HTTPException(404, "邀请码无效或已过期")
    db.run("INSERT OR IGNORE INTO group_members(group_id,user_id,joined) VALUES(?,?,?)", (g["id"], u["id"], now()))
    return {"id": g["id"], "name": g["name"]}


@app.post("/api/goals/{gid}/claim_all")
def claim_all(gid: int, u: dict = Depends(current_user)):
    """共同目标:一键认领所有未认领任务。"""
    g = db.row("SELECT * FROM goals WHERE id=?", (gid,))
    if not g or not g["group_id"] or not db.row("SELECT 1 FROM group_members WHERE group_id=? AND user_id=?", (g["group_id"], u["id"])):
        raise HTTPException(404, "目标不存在或不在该小组")
    n = db.run("UPDATE tasks SET user_id=? WHERE goal_id=? AND user_id IS NULL AND status!='done'", (u["id"], gid))
    return {"claimed": n}


@app.get("/api/groups")
def my_groups(u: dict = Depends(current_user)):
    return db.rows("SELECT g.id,g.name,g.owner_id FROM groups g JOIN group_members m ON m.group_id=g.id WHERE m.user_id=?", (u["id"],))


@app.get("/api/groups/{gid}")
def group_detail(gid: int, u: dict = Depends(current_user)):
    if not db.row("SELECT 1 FROM group_members WHERE group_id=? AND user_id=?", (gid, u["id"])):
        raise HTTPException(403, "不在该小组")
    g = db.row("SELECT * FROM groups WHERE id=?", (gid,))
    members = db.rows("SELECT u.id,u.username FROM users u JOIN group_members m ON m.user_id=u.id WHERE m.group_id=?", (gid,))
    t = today()
    group_tasks = db.rows("SELECT * FROM tasks WHERE group_id=?", (gid,))
    for m in members:
        mine = [x for x in group_tasks if x["user_id"] == m["id"]]   # 只算共同目标下他认领的任务
        done = [x for x in mine if x["status"] == "done"]
        m["claimed"] = len(mine); m["done"] = len(done)
        m["completion_rate"] = round(len(done) / len(mine), 2) if mine else 0.0
        m["hours_done"] = round(sum(float(x["est_hours"] or 0) for x in done), 1)
        m["streak"] = rules.streak(_done_dates(m["id"]), t)
    goals = db.rows("SELECT * FROM goals WHERE group_id=? ORDER BY (status!='active'), id DESC", (gid,))
    names = {m["id"]: m["username"] for m in members}
    for gl in goals:
        gl["tasks"] = db.rows("SELECT * FROM tasks WHERE goal_id=? ORDER BY due, id", (gl["id"],))
        for x in gl["tasks"]:
            x["assignee"] = names.get(x["user_id"])
        done = sum(1 for x in gl["tasks"] if x["status"] == "done")
        gl["progress"] = round(done / len(gl["tasks"]), 2) if gl["tasks"] else 0.0
        gl["days_left"] = (rules.d(gl["due"]) - t).days if gl["due"] else None
    members.sort(key=lambda m: (-m["completion_rate"], -m["hours_done"], -m["done"], m["username"]))   # 组内排行
    for i, m in enumerate(members, 1):
        m["rank"] = i
    group_tasks_free = [x for x in group_tasks if not x["goal_id"]]
    for x in group_tasks_free:
        x["assignee"] = names.get(x["user_id"])
    group_tasks_free.sort(key=lambda x: (x["status"] == "done", x["due"] or "9999", x["id"]))
    return {"id": g["id"], "name": g["name"], "invite_code": g["invite_code"] if g["owner_id"] == u["id"] else None,
            "members": members, "goals": goals, "group_tasks": group_tasks_free}


# ── 静态页 ──────────────────────────────────────────────────────────────

@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"), headers={"Cache-Control": "no-cache"})


@app.middleware("http")
async def _no_cache_static(request: Request, call_next):
    """静态文件每次回源校验:曾因浏览器缓存旧 app.js 配新 index.html 导致整页脚本崩掉。"""
    resp = await call_next(request)
    if request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


app.mount("/static", StaticFiles(directory=STATIC), name="static")
