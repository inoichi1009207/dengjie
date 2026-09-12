"""模型层:DeepSeek 官方(OpenAI 兼容)。没配 key 时走桩,整个应用照常可用。

环境变量:LLM_API_KEY(或 DEEPSEEK_API_KEY)、LLM_BASE_URL(默认 https://api.deepseek.com)、LLM_MODEL(默认 deepseek-chat)
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re

_BASE = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com")
_MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")
_KEY_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "apikey.txt")


def _load_key() -> str:
    """优先环境变量;其次项目根 apikey.txt(已 gitignore)。key 永不打印。"""
    k = os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or ""
    if not k and os.path.exists(_KEY_FILE):
        with open(_KEY_FILE, encoding="utf-8") as f:
            k = f.read().strip().splitlines()[0].strip() if f else ""
    return k


_KEY = _load_key()


def available() -> bool:
    return bool(_KEY) and os.environ.get("LLM_FORCE_STUB") != "1"


def _chat_json(system: str, user: str) -> dict:
    from openai import OpenAI
    client = OpenAI(base_url=_BASE, api_key=_KEY)
    r = client.chat.completions.create(
        model=_MODEL, temperature=0.2,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    return json.loads(r.choices[0].message.content or "{}")


# ── 目标拆解 ──────────────────────────────────────────────────────────────

_DECOMPOSE_SYS = (
    "你是交大学生的学习规划助手。把用户的长期目标拆成 4–10 个可执行任务。"
    "只输出 JSON:{\"tasks\":[{\"title\":str,\"est_hours\":number,\"due\":\"YYYY-MM-DD\",\"resource\":str|null}]}。"
    "任务要具体到能直接开始做;est_hours 是诚实的小时估计,**单个任务不超过 8 小时**,更大的内容按周拆成多个任务;"
    "due 不晚于目标截止日、按顺序递增、尽量分散到不同周;"
    "resource 只写公开课/教材章节名,不编造链接;不确定就写 null。"
)


def decompose_goal(title: str, due: str | None, today: dt.date) -> list[dict]:
    if available():
        try:
            out = _chat_json(_DECOMPOSE_SYS, f"今天 {today.isoformat()};目标:{title};截止:{due or '未定'}")
            tasks = out.get("tasks") or []
            if tasks:
                return [_norm_task(t) for t in tasks][:12]
        except Exception as e:  # 模型挂了退回桩,不让按钮死掉
            print("[llm] decompose failed:", e)
    return _decompose_stub(title, due, today)


_DISCUSS_SYS = _DECOMPOSE_SYS + (
    "\n这是一次多轮讨论:用户会对上一版任务树提意见(合并、拆细、改时长、换顺序、加减内容等)。"
    "每次都输出**完整的新版**任务列表,并在 \"note\" 字段用一句话说明你改了什么。"
    "输出 JSON:{\"note\":str,\"tasks\":[...]}。"
)


def discuss_goal(title: str, due: str | None, today: dt.date, history: list[dict], feedback: str) -> dict:
    """多轮讨论拆解:history 是 [{role, content}](content 为纯文本);返回 {note, tasks}。"""
    if available():
        try:
            from openai import OpenAI
            client = OpenAI(base_url=_BASE, api_key=_KEY)
            msgs = [{"role": "system", "content": _DISCUSS_SYS},
                    {"role": "user", "content": f"今天 {today.isoformat()};目标:{title};截止:{due or '未定'}"}]
            msgs += [{"role": m.get("role", "user"), "content": str(m.get("content", ""))[:4000]} for m in history[-10:]]
            msgs.append({"role": "user", "content": feedback})
            r = client.chat.completions.create(model=_MODEL, temperature=0.3, response_format={"type": "json_object"}, messages=msgs)
            out = json.loads(r.choices[0].message.content or "{}")
            tasks = [_norm_task(t) for t in (out.get("tasks") or [])][:12]
            if tasks:
                return {"note": str(out.get("note") or "已按你的意见调整。"), "tasks": tasks}
        except Exception as e:
            print("[llm] discuss failed:", e)
    # 桩:认「合并/少一点」「拆细/多一点」两类意见
    base = _decompose_stub(title, due, today)
    if re.search(r"少|合并|精简|太多", feedback):
        base = base[:2]
    elif re.search(r"多|拆细|细一点|太少", feedback):
        base = base + [{"title": f"{title} · 补充段", "est_hours": 2.0, "due": due, "resource": None}]
    return {"note": "(示例模式)按关键词粗调了数量。", "tasks": base}


def _norm_task(t: dict) -> dict:
    return {"title": str(t.get("title") or "")[:120], "est_hours": float(t.get("est_hours") or 1),
            "due": t.get("due") or None, "resource": t.get("resource") or None}


def _decompose_stub(title: str, due: str | None, today: dt.date) -> list[dict]:
    end = dt.date.fromisoformat(due) if due else today + dt.timedelta(days=28)
    span = max(4, (end - today).days)
    n = 4
    return [{"title": f"{title} · 第 {i+1}/{n} 段", "est_hours": 3.0,
             "due": (today + dt.timedelta(days=span * (i + 1) // n)).isoformat(),
             "resource": None} for i in range(n)]


# ── 课表解析(路线 1:粘贴文字)────────────────────────────────────────────

_SCHEDULE_SYS = (
    "把用户粘贴的交大课表文字解析成 JSON:{\"slots\":[{\"name\":str,\"teacher\":str|null,\"location\":str|null,"
    "\"day\":1-7,\"slot_start\":int,\"slot_end\":int,\"weeks\":[int]}]}。"
    "day 1=周一;节次如「3-4节」→ slot_start 3, slot_end 4;周次「1-16周」展开成列表,「单/双」只取奇/偶周。解析不出的行跳过。"
)

_DAY = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}


def parse_schedule_text(text: str) -> list[dict]:
    if available():
        try:
            out = _chat_json(_SCHEDULE_SYS, text[:8000])
            slots = [s for s in (out.get("slots") or []) if s.get("name") and s.get("day")]
            if slots:
                return [_norm_slot(s) for s in slots]
        except Exception as e:
            print("[llm] parse_schedule failed:", e)
    return parse_schedule_regex(text)


def _norm_slot(s: dict) -> dict:
    return {"name": str(s["name"])[:80], "teacher": s.get("teacher"), "location": s.get("location"),
            "day": int(s["day"]), "slot_start": int(s.get("slot_start") or 1), "slot_end": int(s.get("slot_end") or 2),
            "weeks": [int(w) for w in (s.get("weeks") or list(range(1, 17)))]}


def expand_weeks(zcd: str) -> list[int]:
    weeks: set[int] = set()
    for part in re.split(r"[,,、]", zcd):
        m = re.search(r"(\d+)\s*(?:-\s*(\d+))?", part)
        if not m:
            continue
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        rng = range(a, b + 1)
        if "单" in part:
            rng = [w for w in rng if w % 2 == 1]
        elif "双" in part:
            rng = [w for w in rng if w % 2 == 0]
        weeks.update(rng)
    return sorted(weeks) or list(range(1, 17))


_LINE = re.compile(
    r"(?P<name>[^\s，,]{2,40}?)\s*[，,]?\s*(?:星期|周)(?P<day>[一二三四五六日天])\s*(?:第?\s*(?P<a>\d+)\s*[-–~]\s*(?P<b>\d+)\s*节)?"
    r"[^\d]*(?P<weeks>\d+\s*[-–~]\s*\d+\s*周(?:\s*[(（]?[单双][)）]?)?(?:\s*[,，]\s*\d+\s*[-–~]\s*\d+\s*周)*)?"
)


def parse_schedule_regex(text: str) -> list[dict]:
    """桩/兜底:识别形如「高等数学 周一 3-4节 1-16周 东上院101 张三」的行。"""
    slots = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _LINE.search(line)
        if not m:
            continue
        a, b = m.group("a"), m.group("b")
        rest = line[m.end():].strip()
        slots.append({"name": m.group("name"), "teacher": None, "location": rest.split()[0] if rest else None,
                      "day": _DAY[m.group("day")], "slot_start": int(a) if a else 1, "slot_end": int(b) if b else 2,
                      "weeks": expand_weeks(m.group("weeks") or "1-16周")})
    return slots


# ── 复盘文案 ──────────────────────────────────────────────────────────────

def replan_sentence(plan: dict) -> str:
    titles = plan.get("tomorrow_titles") or []
    n_over = len(plan.get("overflow") or [])
    base = ("明天只剩:" + "、".join(titles)) if titles else "明天清空,好好休息"
    if n_over:
        base += f";另有 {n_over} 件本周放不下,等你取舍"
    if not available():
        return base + "。"
    try:
        out = _chat_json("把这段安排改写成一句温和、不超过 40 字的中文,只输出 JSON {\"text\":str}", base)
        return out.get("text") or base + "。"
    except Exception:
        return base + "。"
