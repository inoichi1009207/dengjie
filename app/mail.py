"""交大邮箱接入:IMAP 取最近 N 天 → 字段白名单清洗 → 模型抽「疑似任务」。

数据边界(PRD 第四版):只把「主题、日期、清洗后的正文片段(去邮箱地址/手机号/URL/引用/签名)」交给模型;
原文不持久化、不进日志。演示账号见 README。
"""
from __future__ import annotations

import datetime as dt
import email
import imaplib
import json
import os
import re
from email.header import decode_header, make_header

IMAP_HOST = os.environ.get("MAIL_IMAP_HOST", "mail.sjtu.edu.cn")
IMAP_PORT = int(os.environ.get("MAIL_IMAP_PORT", "993"))

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<!\d)1\d{10}(?!\d)")
_URL = re.compile(r"https?://\S+")
_CUT = re.compile(r"^(?:-{3,}\s*原始邮件|-{3,}\s*Original Message|From:|发件人[:：]|On .+ wrote:|在 .+ 写道)", re.M)


def _decode(s) -> str:
    try:
        return str(make_header(decode_header(s or "")))
    except Exception:
        return str(s or "")


def _body_text(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get("Content-Disposition"):
                return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "ignore")
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                html = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "ignore")
                return re.sub(r"<[^>]+>", " ", html)
        return ""
    payload = msg.get_payload(decode=True)
    return payload.decode(msg.get_content_charset() or "utf-8", "ignore") if payload else ""


def fetch_recent(user: str, password: str, days: int = 7, limit: int = 30) -> list[dict]:
    """返回原始邮件(仅内存使用):[{uid, subject, date, from_domain, body}]"""
    since = (dt.date.today() - dt.timedelta(days=days)).strftime("%d-%b-%Y")
    box = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    try:
        box.login(user if "@" in user else f"{user}@sjtu.edu.cn", password)
        box.select("INBOX", readonly=True)
        _, data = box.uid("search", None, f"(SINCE {since})")
        uids = (data[0] or b"").split()[-limit:]
        out = []
        for uid in reversed(uids):
            _, msgdata = box.uid("fetch", uid, "(RFC822)")
            raw = next((p[1] for p in msgdata if isinstance(p, tuple)), None)
            if not raw:
                continue
            msg = email.message_from_bytes(raw)
            frm = _decode(msg.get("From"))
            m = _EMAIL.search(frm)
            out.append({"uid": uid.decode(), "subject": _decode(msg.get("Subject")), "date": (msg.get("Date") or "")[:31],
                        "from_domain": m.group(0).split("@")[-1].lower() if m else "", "body": _body_text(msg)})
        return out
    finally:
        try:
            box.logout()
        except Exception:
            pass


def clean(mail: dict, max_chars: int = 1200) -> dict:
    """白名单:主题、日期、发件域、清洗后的正文片段。任何地址/手机号/URL 一律剥掉。"""
    body = mail.get("body") or ""
    cut = _CUT.search(body)
    if cut:
        body = body[:cut.start()]
    body = "\n".join(l for l in body.splitlines() if not l.strip().startswith(">"))
    body = _URL.sub("[链接]", body)
    body = _EMAIL.sub("[邮箱]", body)
    body = _PHONE.sub("[电话]", body)
    body = re.sub(r"[ \t]+", " ", body).strip()[:max_chars]
    return {"uid": mail["uid"], "subject": _EMAIL.sub("[邮箱]", mail.get("subject") or ""),
            "date": mail.get("date") or "", "from_domain": mail.get("from_domain") or "", "excerpt": body}


_KEYWORDS = re.compile(r"作业|报告|提交|截止|DDL|deadline|考试|测验|实验|签到|选课|答辩|论文|评审|问卷|申请", re.I)
_DATE = re.compile(r"(?:(\d{4})[年./-])?(\d{1,2})[月./-](\d{1,2})(?:日|号)?")


def _norm_date(m: re.Match, today: dt.date) -> str | None:
    try:
        y = int(m.group(1) or today.year)
        d = dt.date(y, int(m.group(2)), int(m.group(3)))
        if not m.group(1) and d < today - dt.timedelta(days=30):
            d = d.replace(year=y + 1)
        return d.isoformat()
    except ValueError:
        return None


def extract_tasks_stub(cleaned: list[dict], today: dt.date) -> list[dict]:
    out = []
    for c in cleaned:
        text = f"{c['subject']}\n{c['excerpt']}"
        if not _KEYWORDS.search(text):
            continue
        m = _DATE.search(text)
        out.append({"title": c["subject"][:100] or "邮件事项", "due": _norm_date(m, today) if m else None,
                    "est_hours": 2.0, "uid": c["uid"]})
    return out


_SYS = (
    "你从清洗过的邮件片段里抽取学生需要**动手完成**的事项(作业、报告、报名、提交、考试准备等),通知类不算。"
    "只输出 JSON:{\"tasks\":[{\"uid\":str,\"title\":str,\"due\":\"YYYY-MM-DD\"|null,\"est_hours\":number}]}。"
    "title 用中文一句话说清要做什么;due 只在片段里有明确日期时给;est_hours 是诚实估计;没有事项就输出空列表。"
)


def extract_tasks(cleaned: list[dict], today: dt.date) -> list[dict]:
    from . import llm
    if not cleaned:
        return []
    if llm.available():
        try:
            payload = json.dumps([{"uid": c["uid"], "subject": c["subject"], "date": c["date"], "excerpt": c["excerpt"]} for c in cleaned], ensure_ascii=False)
            out = llm._chat_json(_SYS, f"今天 {today.isoformat()}\n{payload}")
            tasks = [t for t in (out.get("tasks") or []) if t.get("title")]
            return [{"uid": str(t.get("uid") or ""), "title": str(t["title"])[:120], "due": t.get("due") or None,
                     "est_hours": float(t.get("est_hours") or 2)} for t in tasks]
        except Exception as e:
            print("[mail] llm extract failed:", e)
    return extract_tasks_stub(cleaned, today)
