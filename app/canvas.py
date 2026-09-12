"""Canvas 拉取(演示账号令牌)。端点与 sjtu-agent canvas_client.py 一致。"""
from __future__ import annotations

import os

import requests

CANVAS_BASE = os.environ.get("CANVAS_BASE", "https://oc.sjtu.edu.cn")


def _get(path: str, token: str, params: dict | None = None):
    r = requests.get(f"{CANVAS_BASE}/api/v1{path}", headers={"Authorization": f"Bearer {token}"},
                     params=params or {}, timeout=20)
    r.raise_for_status()
    return r.json()


def fetch_pending(token: str) -> list[dict]:
    """返回 [{external_id, title, due, course, est_hours}],来自 users/self/todo 与 planner/items。"""
    out: dict[str, dict] = {}
    for item in _get("/users/self/todo", token):
        a = item.get("assignment") or {}
        if not a:
            continue
        key = f"canvas:assignment:{a.get('id')}"
        out[key] = {"external_id": key, "title": a.get("name") or "Canvas 作业",
                    "due": (a.get("due_at") or "")[:10] or None, "course": item.get("context_name"), "est_hours": 2.0}
    for item in _get("/planner/items", token, {"per_page": 50}):
        if item.get("plannable_type") != "assignment":
            continue
        p = item.get("plannable") or {}
        key = f"canvas:assignment:{p.get('id') or item.get('plannable_id')}"
        out.setdefault(key, {"external_id": key, "title": p.get("title") or "Canvas 作业",
                             "due": (p.get("due_at") or "")[:10] or None,
                             "course": item.get("context_name"), "est_hours": 2.0})
    return list(out.values())
