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
    client = OpenAI(base_url=_BASE, api_key=_KEY, timeout=45, max_retries=1)
    r = client.chat.completions.create(
        model=_MODEL, temperature=0.2,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    return json.loads(r.choices[0].message.content or "{}")


# ── 目标拆解 ──────────────────────────────────────────────────────────────

_DECOMPOSE_SYS = (
    "你是交大学生的学习规划助手。把用户的长期目标拆成 4–14 个可执行任务。"
    "只输出 JSON:{\"tasks\":[{\"title\":str,\"est_hours\":number,\"due\":\"YYYY-MM-DD\",\"resource\":str|null}]}。"
    "任务要具体到能直接开始做;est_hours 是诚实的小时估计,**单个任务不超过 3 小时**,更大的内容拆成多个 ≤3 小时的任务;"
    "due 不晚于目标截止日、按顺序递增、尽量分散到不同周;"
    "resource 只写公开课/教材章节名,不编造链接;不确定就写 null。"
)


def _keywords(title: str) -> list[str]:
    """从目标里挑值得联网查一下的对象:书名号内容、英文专有名词、课程编号。"""
    ks = re.findall(r"[《〈]([^》〉]{2,60})[》〉]", title)
    ks += re.findall(r"\b(?:MIT|Stanford|CS|EE|Math)?\s?\d{2,3}\.\d{2,3}[A-Z]*\b", title)
    ks += re.findall(r"\b[A-Z][A-Za-z0-9+#.-]{2,}(?:\s+[A-Z][A-Za-z0-9+#.-]{1,}){0,4}\b", title)
    seen, out = set(), []
    for k in ks:
        k = k.strip()
        if k and k.lower() not in seen:
            seen.add(k.lower()); out.append(k)
    # 去掉被更长关键词包含的短词(「Analysis」⊂「Analysis I」)
    out = [k for k in out if not any(k != o and k.lower() in o.lower() for o in out)]
    return out[:3]


def enrich(title: str, timeout: float = 4.0) -> list[dict]:
    """联网查关键词(维基百科 REST 摘要,英文优先、中文兜底),返回 [{term, title, extract, url}]。查不到就空;网络错误不抛。"""
    import requests
    out = []
    for term in _keywords(title):
        hit = None
        for lang, q in (("en", f'"{term}" textbook'), ("en", term), ("zh", term)):
            try:
                s = requests.get(f"https://{lang}.wikipedia.org/w/api.php",
                                 params={"action": "query", "list": "search", "srsearch": q, "srlimit": 3, "format": "json"},
                                 headers={"User-Agent": "dengjie/0.1"}, timeout=timeout).json()
                for item in (s.get("query") or {}).get("search") or []:
                    page, snippet = item.get("title") or "", re.sub(r"<[^>]+>", "", item.get("snippet") or "")
                    # 命中判据:页面标题或摘要片段里得真出现这个词,避免「Analysis I」对到「Analyser」
                    if term.lower() in page.lower() or term.lower() in snippet.lower():
                        r = requests.get(f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(page)}",
                                         headers={"User-Agent": "dengjie/0.1"}, timeout=timeout).json()
                        extract = (r.get("extract") or "").strip()
                        if extract:
                            hit = {"term": term, "title": r.get("title") or page, "extract": extract[:600],
                                   "url": (r.get("content_urls") or {}).get("desktop", {}).get("page")}
                            break
                if hit:
                    break
            except Exception:
                continue
        if hit:
            out.append(hit)
    return out


_CATALOG_PATH = os.path.join(os.path.dirname(__file__), "resources.json")


def catalog_match(title: str) -> list[dict]:
    """预置资源目录:目标标题命中关键词的条目。"""
    try:
        with open(_CATALOG_PATH, encoding="utf-8") as f:
            items = json.load(f).get("items") or []
    except OSError:
        return []
    low = title.lower()
    return [it for it in items if any(k.lower() in low for k in it.get("keywords", []))][:4]


def tag_resources(tasks: list[dict], catalog: list[dict]) -> list[dict]:
    """资源只认目录:命中目录条目 → 换成目录标题并附 url;其余保留原文但标「未核验」。"""
    titles = {it["title"]: it for it in catalog}
    for t in tasks:
        r = (t.get("resource") or "").strip()
        if not r:
            t["resource_verified"] = None
            continue
        hit = next((it for it in catalog if it["title"] == r or r.lower() in it["title"].lower() or any(k.lower() in r.lower() for k in it["keywords"])), None)
        if hit:
            t["resource"] = hit["title"]; t["resource_url"] = hit["url"]; t["resource_verified"] = True
        else:
            t["resource"] = r + "(未核验)" if "(未核验)" not in r else r; t["resource_verified"] = False
    return tasks


def decompose_goal(title: str, due: str | None, today: dt.date, context: list[dict] | None = None) -> list[dict]:
    catalog = catalog_match(title)
    if available():
        try:
            ctx = ""
            if context:
                ctx = "\n参考资料(联网查到,可据此把任务写具体,如按章节/单元拆):\n" + "\n".join(f"- {c['title']}:{c['extract'][:400]}" for c in context)
            if catalog:
                ctx += "\n可选资源目录(resource 字段优先从这里选,原样照抄标题):\n" + "\n".join(f"- {it['title']}" for it in catalog)
            out = _chat_json(_DECOMPOSE_SYS, f"今天 {today.isoformat()};目标:{title};截止:{due or '未定'}{ctx}")
            tasks = out.get("tasks") or []
            if tasks:
                return tag_resources([_norm_task(t) for t in tasks][:16], catalog)
        except Exception as e:  # 模型挂了退回桩,不让按钮死掉
            print("[llm] decompose failed:", e)
    return tag_resources(_decompose_stub(title, due, today), catalog)


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
            client = OpenAI(base_url=_BASE, api_key=_KEY, timeout=45, max_retries=1)
            msgs = [{"role": "system", "content": _DISCUSS_SYS},
                    {"role": "user", "content": f"今天 {today.isoformat()};目标:{title};截止:{due or '未定'}"}]
            msgs += [{"role": m.get("role", "user"), "content": str(m.get("content", ""))[:4000]} for m in history[-10:]]
            msgs.append({"role": "user", "content": feedback})
            r = client.chat.completions.create(model=_MODEL, temperature=0.3, response_format={"type": "json_object"}, messages=msgs)
            out = json.loads(r.choices[0].message.content or "{}")
            tasks = [_norm_task(t) for t in (out.get("tasks") or [])][:16]
            if tasks:
                return {"note": str(out.get("note") or "已按你的意见调整。"), "tasks": tag_resources(tasks, catalog_match(title))}
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
    return {"title": str(t.get("title") or "")[:120], "est_hours": min(3.0, max(0.25, float(t.get("est_hours") or 1))),
            "due": t.get("due") or None, "resource": t.get("resource") or None}


def _decompose_stub(title: str, due: str | None, today: dt.date) -> list[dict]:
    end = dt.date.fromisoformat(due) if due else today + dt.timedelta(days=28)
    span = max(4, (end - today).days)
    n = 4
    return [{"title": f"{title} · 第 {i+1}/{n} 段", "est_hours": 2.0,
             "due": (today + dt.timedelta(days=span * (i + 1) // n)).isoformat(),
             "resource": None} for i in range(n)]


# ── 课表解析(路线 1:粘贴文字)────────────────────────────────────────────

_SCHEDULE_SYS = (
    "把用户给的交大课表(可能是 PDF 抽出的文字、Markdown 表格、OCR 识别的截图文字、或手打的行)解析成 JSON:"
    "{\"slots\":[{\"name\":str,\"teacher\":str|null,\"location\":str|null,\"day\":1-7,\"slot_start\":int,\"slot_end\":int,\"weeks\":[int]}]}。"
    "规则:day 1=周一…7=周日;节次「3-4节」→ slot_start 3, slot_end 4,「5-8节连上」→ 5 到 8;"
    "周次「1-16周」展开成列表,「单」只取奇数周、「双」只取偶数周,「1-8,10-16周」合并;"
    "Markdown 表格里一格可能含多行(课名/周次/地点/教师),按表头的星期列和行首的节次确定 day 与节次;"
    "同一门课在不同天各出一条;OCR 文字有错别字时按常识纠正课名;解析不出的内容跳过,不要编造。"
)

_DAY = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}


def merge_adjacent(slots: list[dict]) -> list[dict]:
    """同一天、同课名、同周次、节次相邻(如 5-6 与 7-8)的记录合并成一条(5-8),避免表格分行造成的拆分。"""
    out: list[dict] = []
    for s in sorted(slots, key=lambda x: (x["day"], x["name"], x["slot_start"])):
        last = out[-1] if out else None
        if last and last["day"] == s["day"] and last["name"] == s["name"] and last["weeks"] == s["weeks"] \
                and s["slot_start"] == last["slot_end"] + 1:
            last["slot_end"] = s["slot_end"]
            last["location"] = last["location"] or s.get("location"); last["teacher"] = last["teacher"] or s.get("teacher")
        else:
            out.append(dict(s))
    return out


def parse_schedule_text(text: str) -> list[dict]:
    if available():
        try:
            out = _chat_json(_SCHEDULE_SYS, text[:8000])
            slots = [s for s in (out.get("slots") or []) if s.get("name") and s.get("day")]
            if slots:
                return merge_adjacent([_norm_slot(s) for s in slots])
        except Exception as e:
            print("[llm] parse_schedule failed:", e)
    return merge_adjacent(parse_schedule_regex(text))


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


# ── 太累了:只给建议的对话 ───────────────────────────────────────────────

_TIRED_SYS = (
    "你是交大学生的学习规划助手。用户今天说「太累了」并写了原因。你**只给建议,不替他改任何安排**:"
    "结合他今天没做完的任务、明天的安排上限和原因,用 3–5 条短句说:今晚该不该硬撑、明天怎么取舍、哪件事可以推或删、要不要调低本周承载力;"
    "如果原因里有身体或情绪信号,先建议休息。中文,不超过 150 字,不要客套。"
    "只输出 JSON:{\"advice\":str}。"
)


def tired_advice(reason: str, plan: dict, history: list[dict] | None = None) -> str:
    facts = (f"今天未完成:{'、'.join(m['title'] for m in plan.get('moves', []) if m.get('from'))or '无'};"
             f"明天上限 {plan.get('tomorrow_cap')} 小时;明天将只剩:{'、'.join(plan.get('tomorrow_titles') or []) or '无'};"
             f"本周放不下:{'、'.join(o['title'] for o in plan.get('overflow', [])) or '无'}")
    if available():
        try:
            from openai import OpenAI
            client = OpenAI(base_url=_BASE, api_key=_KEY, timeout=45, max_retries=1)
            msgs = [{"role": "system", "content": _TIRED_SYS}, {"role": "user", "content": f"安排事实:{facts}"}]
            msgs += [{"role": m.get("role", "user"), "content": str(m.get("content", ""))[:1000]} for m in (history or [])[-6:]]
            msgs.append({"role": "user", "content": f"我太累了,原因:{reason}"})
            r = client.chat.completions.create(model=_MODEL, temperature=0.4, response_format={"type": "json_object"}, messages=msgs)
            out = json.loads(r.choices[0].message.content or "{}")
            if out.get("advice"):
                return str(out["advice"])[:400]
        except Exception as e:
            print("[llm] tired_advice failed:", e)
    return f"(示例模式)今晚别硬撑。明天先做截止最近的一件;{facts.split(';')[2] if ';' in facts else ''}。连着累两天就把本周承载力调低两成。"


# ── 每日复盘一句点评 ───────────────────────────────────────────────────

def review_comment(stats: dict, note: str) -> str:
    base = f"完成 {stats.get('done', 0)} 件、推迟 {stats.get('postponed', 0)} 件,连续 {stats.get('streak', 0)} 天。"
    if not available():
        return base + ("记下来就是进步。" if note else "")
    try:
        out = _chat_json("你是温和但不客套的学习教练。根据今天的数据和用户一句话复盘,回一句不超过 40 字的中文点评,指出一个明天可以改的点。只输出 JSON {\"text\":str}",
                         f"数据:{base} 用户复盘:{note or '(没写)'}")
        return out.get("text") or base
    except Exception:
        return base


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
