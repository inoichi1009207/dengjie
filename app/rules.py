"""纯规则层:周界、容量缺口、今日清单、「太累了」重排、连续天数。零 IO,便于单测。

对应 PRD 第四版:
  周容量缺口 = 本周承诺任务的剩余估时合计 − 本周可投入时长
  「太累了」= 今日未完成全部顺延;明日上限 = 明日可投入 × 0.6;按(截止日最近, 剩余估时最小)选入;
             放不下的推到本周其余日期;仍放不下的进「本周溢出」;先预览后生效。
"""
from __future__ import annotations

import datetime as dt
from typing import Iterable

SLOT_HOURS = 0.75          # 一节课 45 分钟
WEEKLY_BASE_HOURS = 42.0   # 建议可投入时长的基准(每天 6 小时)
TIRED_FACTOR = 0.6


def d(s: str | None) -> dt.date | None:
    return dt.date.fromisoformat(s) if s else None


def week_bounds(day: dt.date) -> tuple[dt.date, dt.date]:
    mon = day - dt.timedelta(days=day.weekday())
    return mon, mon + dt.timedelta(days=6)


def week_number(semester_start: dt.date | None, day: dt.date) -> int | None:
    """教学周号。学期开始日可以是任意一天(如 9.13 周日),按它所在周的周一起算。"""
    if not semester_start:
        return None
    # 周一到周六开始:该周即第 1 周;周日开始(如 9.13 报到):次日周一起算第 1 周
    if semester_start.weekday() == 6:
        start_mon = semester_start + dt.timedelta(days=1)
    else:
        start_mon = semester_start - dt.timedelta(days=semester_start.weekday())
    n = (day - start_mon).days // 7 + 1
    return n if n >= 1 else None


def class_hours_for_week(slots: Iterable[dict], week_no: int | None) -> float:
    total = 0.0
    for s in slots:
        weeks = s.get("weeks") or []
        if week_no is not None and weeks and week_no not in weeks:
            continue
        a, b = s.get("slot_start") or 0, s.get("slot_end") or 0
        n = (b - a + 1) if a and b and b >= a else 2
        total += n * SLOT_HOURS
    return round(total, 2)


def suggested_weekly_hours(class_hours: float, base: float = WEEKLY_BASE_HOURS) -> float:
    return round(max(0.0, base - class_hours), 1)


def is_open(t: dict) -> bool:
    return t.get("status") == "confirmed"


def committed_this_week(tasks: Iterable[dict], today: dt.date) -> list[dict]:
    """本周承诺 = 已确认未完成,且 (计划日在本周) 或 (截止日在本周) 或 (已逾期)。"""
    mon, sun = week_bounds(today)
    out = []
    for t in tasks:
        if not is_open(t):
            continue
        sd, due = d(t.get("scheduled_date")), d(t.get("due"))
        if (sd and mon <= sd <= sun) or (due and mon <= due <= sun) or (due and due < mon) or (sd and sd < mon):
            out.append(t)
    return out


def capacity_gap(tasks: Iterable[dict], weekly_hours: float, today: dt.date) -> dict:
    committed = committed_this_week(tasks, today)
    hours = round(sum(float(t.get("remaining_hours") or 0) for t in committed), 2)
    return {"committed_hours": hours, "weekly_hours": weekly_hours,
            "gap": round(hours - weekly_hours, 2), "task_ids": [t["id"] for t in committed]}


def today_tasks(tasks: Iterable[dict], today: dt.date) -> list[dict]:
    out = []
    for t in tasks:
        if not is_open(t):
            continue
        sd, due = d(t.get("scheduled_date")), d(t.get("due"))
        if (sd and sd <= today) or (due and due <= today and not sd):
            out.append(t)
    out.sort(key=lambda t: (t.get("due") or "9999", -float(t.get("remaining_hours") or 0)))
    return out


def too_tired_plan(tasks: Iterable[dict], today: dt.date, daily_hours: float) -> dict:
    """返回预览:{moves:[{id,title,from,to}], overflow:[...], tomorrow_cap}。不改输入。"""
    tomorrow = today + dt.timedelta(days=1)
    _, sun = week_bounds(today)
    cap = round(daily_hours * TIRED_FACTOR, 2)
    todays = today_tasks(tasks, today)
    tomorrows = [t for t in tasks if is_open(t) and d(t.get("scheduled_date")) == tomorrow]
    pool = {t["id"]: t for t in todays + tomorrows}
    ordered = sorted(pool.values(), key=lambda t: (t.get("due") or "9999", float(t.get("remaining_hours") or 0)))
    # 明日先装,装不下往后推;每天上限 = daily_hours(明日按 cap)
    days = [tomorrow] + [tomorrow + dt.timedelta(days=i) for i in range(1, 7) if tomorrow + dt.timedelta(days=i) <= sun]
    caps = {day: (cap if day == tomorrow else daily_hours) for day in days}
    load = {day: 0.0 for day in days}
    moves, overflow = [], []
    for t in ordered:
        h = float(t.get("remaining_hours") or 0)
        placed = None
        for day in days:
            # 装得下就装;超过单日上限但不超过两倍的大任务,允许独占明天之后的某个空白日
            if load[day] + h <= caps[day] + 1e-9 or (load[day] == 0 and day != tomorrow and daily_hours < h <= 2 * daily_hours):
                placed = day; break
        if placed is None:
            overflow.append({"id": t["id"], "title": t["title"], "hours": h, "due": t.get("due")})
            continue
        load[placed] += h
        moves.append({"id": t["id"], "title": t["title"], "hours": h,
                      "from": t.get("scheduled_date"), "to": placed.isoformat()})
    return {"tomorrow": tomorrow.isoformat(), "tomorrow_cap": cap, "moves": moves, "overflow": overflow,
            "tomorrow_titles": [m["title"] for m in moves if m["to"] == tomorrow.isoformat()]}


def streak(done_dates: Iterable[str], today: dt.date) -> int:
    s = {d(x) for x in done_dates if x}
    n, day = 0, today
    if today not in s:           # 今天还没完成,从昨天数
        day = today - dt.timedelta(days=1)
    while day in s:
        n += 1
        day -= dt.timedelta(days=1)
    return n


def completion_rate(tasks: Iterable[dict], today: dt.date) -> float:
    mon, sun = week_bounds(today)
    done = 0; total = 0
    for t in tasks:
        if t.get("status") == "done" and t.get("done_at") and mon <= d(t["done_at"][:10]) <= sun:
            done += 1; total += 1
        elif is_open(t):
            sd, due = d(t.get("scheduled_date")), d(t.get("due"))
            if (sd and sd <= sun) or (due and due <= sun):
                total += 1
    return round(done / total, 2) if total else 0.0
