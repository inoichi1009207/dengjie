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


def _demo_creds() -> dict:
    """演示账号:环境变量优先;其次 DENGJIE_DEMO_CREDS 指向的文件(四行:Canvas 令牌 / 空 / 邮箱账号 / 密码)。
    文件已 gitignore;值永不打印、永不进日志。"""
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
                  os.environ.get("DEFAULT_SEMESTER_START", "2026-09-13"), now()))
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
            "has_canvas_token": bool(u["canvas_token"] or _demo_creds()["canvas"]),
            "has_mail": bool((u.get("mail_user") and u.get("mail_pass")) or (_demo_creds()["mail_user"] and _demo_creds()["mail_pass"])),
            "mail_user": u.get("mail_user") or _demo_creds()["mail_user"],
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
        db.run("UPDATE users SET weekly_hours=? WHERE id=?", (max(0.0, s.weekly_hours), u["id"]))
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

class GoalIn(BaseModel):
    title: str
    due: str | None = None


class TaskIn(BaseModel):
    title: str
    est_hours: float = 1.0
    due: str | None = None
    resource: str | None = None
    scheduled_date: str | None = None
    goal_id: int | None = None


class GoalCreate(GoalIn):
    tasks: list[TaskIn]
    group_id: int | None = None


@app.post("/api/goals/decompose")
def decompose(g: GoalIn, u: dict = Depends(current_user)):
    return {"tasks": llm.decompose_goal(g.title, g.due, today()), "llm": "deepseek" if llm.available() else "stub"}


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
    if s.action == "achieved":
        db.run("UPDATE goals SET status='achieved', settled_at=? WHERE id=?", (now(), gid))
        db.run("UPDATE tasks SET status='done', remaining_hours=0, done_at=COALESCE(done_at,?) WHERE goal_id=? AND status!='done'", (now(), gid))
    elif s.action == "extend":
        if not s.new_due:
            raise HTTPException(400, "延期要给新截止日")
        dt.date.fromisoformat(s.new_due)
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
大学物理实验 周五 5-8节 3-14周(单) 物理实验中心"""


@app.post("/api/demo/reset")
def demo_reset(u: dict = Depends(current_user)):
    """清空当前账号的个人数据(不动小组)。"""
    for sql in ("DELETE FROM tasks WHERE user_id=? AND group_id IS NULL", "DELETE FROM goals WHERE user_id=?",
                "DELETE FROM schedule_slots WHERE user_id=?", "DELETE FROM progress_events WHERE user_id=?",
                "DELETE FROM fatigue_events WHERE user_id=?"):
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
        g["days_left"] = (rules.d(g["due"]) - t).days
        g["tasks_total"] = len(ts); g["tasks_done"] = sum(1 for x in ts if x["status"] == "done")
    tasks = [x for x in _my_tasks(u["id"]) if x["status"] == "confirmed" and x["due"]]
    for x in tasks:
        x["days_left"] = (rules.d(x["due"]) - t).days
    tasks.sort(key=lambda x: x["due"])
    return {"today": t.isoformat(), "due_goals": [g for g in goals if g["days_left"] <= 0],
            "upcoming_goals": [g for g in goals if g["days_left"] > 0], "tasks": tasks[:50]}


def _insert_task(user_id: int | None, t: TaskIn, source: str, goal_id: int | None = None,
                 group_id: int | None = None, status: str = "confirmed", external_id: str | None = None) -> int:
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


def _own_task(tid: int, uid: int) -> dict:
    t = db.row("SELECT * FROM tasks WHERE id=?", (tid,))
    if not t or (t["user_id"] != uid and not (t["group_id"] and t["user_id"] is None)):
        raise HTTPException(404, "任务不存在")
    return t


@app.patch("/api/tasks/{tid}")
def patch_task(tid: int, p: TaskPatch, u: dict = Depends(current_user)):
    _own_task(tid, u["id"])
    for k, v in p.model_dump(exclude_none=True).items():
        if k == "status" and v not in ("pending", "confirmed", "done"):
            raise HTTPException(400, "status 非法")
        db.run(f"UPDATE tasks SET {k}=? WHERE id=?", (v, tid))
        if k == "est_hours":
            db.run("UPDATE tasks SET remaining_hours=MIN(remaining_hours,?) WHERE id=?", (v, tid))
    return db.row("SELECT * FROM tasks WHERE id=?", (tid,))


@app.delete("/api/tasks/{tid}")
def delete_task(tid: int, u: dict = Depends(current_user)):
    _own_task(tid, u["id"])
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
    if not t or not t["group_id"] or t["user_id"] is not None:
        raise HTTPException(400, "不可认领")
    if not db.row("SELECT 1 FROM group_members WHERE group_id=? AND user_id=?", (t["group_id"], u["id"])):
        raise HTTPException(403, "不在该小组")
    db.run("UPDATE tasks SET user_id=? WHERE id=?", (u["id"], tid))
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
    if sched:
        slots = [SlotIn(**{k: s.get(k) for k in ("name", "teacher", "location", "day", "slot_start", "slot_end", "weeks") if s.get(k) is not None}) for s in sched if s.get("name") and s.get("day")]
        out["schedule"] = _replace_slots(u["id"], slots)
        out["weekly_hours"] = _adopt_suggested(u)
    for key, source in (("canvas_tasks", "canvas"), ("mail_tasks", "email")):
        for it in obj.get(key) or []:
            title = str(it.get("title") or "").strip()
            if not title:
                continue
            ext = str(it.get("external_id") or f"{source}:{title[:40]}")
            if db.row("SELECT 1 FROM tasks WHERE user_id=? AND external_id=?", (u["id"], ext)):
                out["skipped"] += 1; continue
            _insert_task(u["id"], TaskIn(title=title[:120], est_hours=float(it.get("est_hours") or 2), due=it.get("due") or None),
                         source, status="pending", external_id=ext)
            out[key] += 1
    return out


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


def _adopt_suggested(u: dict) -> float:
    """按课表推算本周可投入时长(基准 42 − 课时)并写入。"""
    wk = rules.week_number(rules.d(u["semester_start"]), rules.week_bounds(today())[0])
    h = rules.suggested_weekly_hours(rules.class_hours_for_week(_slots(u["id"]), wk))
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


@app.get("/api/week")
def week(date: str | None = None, u: dict = Depends(current_user)):
    day = rules.d(date) or today()
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
    class_hours = rules.class_hours_for_week(slots, wk)
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
    class_hours = rules.class_hours_for_week(slots, wk_now)
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
    return {"date": t.isoformat(), "tasks": rules.today_tasks(tasks, t), "streak": rules.streak(_done_dates(u["id"]), t),
            "pending": [x for x in tasks if x["status"] == "pending"], "classes": classes, "week_no": wk}


@app.post("/api/review/{tid}/done")
def review_done(tid: int, u: dict = Depends(current_user)):
    _own_task(tid, u["id"])
    db.run("UPDATE tasks SET status='done', remaining_hours=0, done_at=? WHERE id=?", (now(), tid))
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
    return {"ok": True, "scheduled_date": new.isoformat(), "past_due": warn}


@app.post("/api/review/too_tired/preview")
def too_tired_preview(u: dict = Depends(current_user)):
    t = today()
    plan = rules.too_tired_plan(_my_tasks(u["id"]), t, u["weekly_hours"] / 7)
    plan["sentence"] = llm.replan_sentence(plan)
    n = db.row("SELECT COUNT(*) c FROM fatigue_events WHERE user_id=? AND date>=?", (u["id"], (t - dt.timedelta(days=2)).isoformat()))["c"]
    plan["advice"] = ("连续三天太累了,要不要把目标截止日往后挪一挪?" if n >= 2 else
                      "连着两天太累了,建议把本周可投入时长下调两成。" if n == 1 else None)
    return plan


class PlanIn(BaseModel):
    moves: list[dict]
    overflow: list[dict] = []


@app.post("/api/review/too_tired/apply")
def too_tired_apply(p: PlanIn, u: dict = Depends(current_user)):
    t = today()
    todays = rules.today_tasks(_my_tasks(u["id"]), t)
    db.run("INSERT INTO fatigue_events(user_id,date,remaining) VALUES(?,?,?)",
           (u["id"], t.isoformat(), sum(float(x["remaining_hours"] or 0) for x in todays)))
    for m in p.moves:
        _own_task(int(m["id"]), u["id"])
        db.run("UPDATE tasks SET scheduled_date=? WHERE id=?", (m["to"], int(m["id"])))
    for o in p.overflow:  # 本周放不下的:取消计划日,回到截止日那格等用户取舍
        _own_task(int(o["id"]), u["id"])
        db.run("UPDATE tasks SET scheduled_date=NULL WHERE id=?", (int(o["id"]),))
    db.run("INSERT INTO progress_events(user_id,task_id,action,at) VALUES(?,?,?,?)", (u["id"], None, "too_tired", now()))
    return {"ok": True, "moved": len(p.moves)}


@app.post("/api/settings/reduce_weekly")
def reduce_weekly(u: dict = Depends(current_user)):
    h = round(u["weekly_hours"] * 0.8, 1)
    db.run("UPDATE users SET weekly_hours=? WHERE id=?", (h, u["id"]))
    return {"weekly_hours": h}


# ── Canvas ──────────────────────────────────────────────────────────────

@app.post("/api/canvas/import")
def canvas_import(u: dict = Depends(current_user)):
    token = u["canvas_token"] or _demo_creds()["canvas"]
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
    user = u.get("mail_user") or _demo_creds()["mail_user"]
    pw = u.get("mail_pass") or _demo_creds()["mail_pass"]
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
    return {"schedule_slots": len(_slots(u["id"])), "canvas": {"configured": bool(u["canvas_token"] or _demo_creds()["canvas"]), "tasks": cnt("canvas")},
            "mail": {"configured": bool((u.get("mail_user") and u.get("mail_pass")) or (_demo_creds()["mail_user"] and _demo_creds()["mail_pass"])),
                     "user": u.get("mail_user") or _demo_creds()["mail_user"], "tasks": cnt("email")},
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
    return {"id": g["id"], "name": g["name"], "invite_code": g["invite_code"] if g["owner_id"] == u["id"] else None,
            "members": members, "goals": goals}


# ── 静态页 ──────────────────────────────────────────────────────────────

@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC), name="static")
