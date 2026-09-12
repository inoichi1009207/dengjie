"""登阶同步器 —— 在用户自己的电脑上跑一次:登 jAccount → 抓教学信息网课表 →(可选)生成 Canvas 令牌 → 上传后端。

为什么跑在用户电脑:服务器 IP 登 jAccount 会触发异地/短信验证(sjtu-agent 排错文档实撞)。

依赖:pip install playwright requests && playwright install chromium
用法:
  python dengjie_sync.py --user <jAccount用户名> [--backend http://localhost:8000] [--canvas-token <已有令牌>] [--headless]
  密码在运行时交互输入,不进命令行、不落盘。
输出:本地 sync_result.json(课表 + 令牌);若给了 --backend 则 POST 到 <backend>/api/sync。

出处(选择器与接口均来自 github.com/kuan-er/sjtu-agent 的 login.py 与 ddl_checker.py):
  jAccount 登录页 https://jaccount.sjtu.edu.cn/jaccount/jalogin
    #input-login-user  #input-login-pass  #captcha-img  #input-login-captcha  #submit-password-button
    二次验证输入框 #input-login-sms-code / #input-bind-sms-code(脚本不自动填,留给人)
  验证码识别 POST https://geek.sjtu.edu.cn/captcha-solver/  files={"image": jpeg}  → json["result"]
  教务课表   POST https://i.sjtu.edu.cn/kbcx/xskbcx_cxXsKb.html  data={"xnm": 学年, "xqm": "3"(秋)|"12"(春)}  → json["kbList"]
    字段:kcmc 课程名 / xm 教师 / cdmc 地点 / xqj 星期 1-7 / jcs 节次 "3-4" / zcd 周次 "1-16周"
Canvas 令牌自动生成走 Canvas 标准设置页(/profile/settings),未在交大实例上逐步核过,失败即回退手动粘贴。
"""
from __future__ import annotations

import argparse
import datetime as dt
import getpass
import json
import re
import sys
import time

import requests
from playwright.sync_api import Page, sync_playwright

JA_LOGIN_ENTRY = "https://i.sjtu.edu.cn/jaccountlogin"
JA_DOMAIN = "jaccount.sjtu.edu.cn"
GEEK_CAPTCHA = "https://geek.sjtu.edu.cn/captcha-solver/"
KB_URL = "https://i.sjtu.edu.cn/kbcx/xskbcx_cxXsKb.html"
CANVAS_BASE = "https://oc.sjtu.edu.cn"


# ── jAccount 登录 ─────────────────────────────────────────────────────────

def solve_captcha(page: Page) -> str:
    img = page.locator("#captcha-img")
    jpeg = img.screenshot(type="jpeg")
    try:
        r = requests.post(GEEK_CAPTCHA, files={"image": ("captcha.jpg", jpeg, "image/jpeg")}, timeout=15)
        code = (r.json().get("result") or "").strip()
        if code:
            print(f"[captcha] 极客协会识别: {code}")
            return code
    except Exception as e:  # 识别服务挂了就手输
        print(f"[captcha] 自动识别失败({e}),请看浏览器窗口手动输入")
    return input("验证码: ").strip()


def jaccount_login(page: Page, username: str, password: str, max_tries: int = 3) -> bool:
    page.goto(JA_LOGIN_ENTRY, wait_until="domcontentloaded")
    for attempt in range(1, max_tries + 1):
        if JA_DOMAIN not in page.url:
            return True
        page.fill("#input-login-user", username)
        page.fill("#input-login-pass", password)
        page.fill("#input-login-captcha", solve_captcha(page))
        page.click("#submit-password-button")
        try:
            page.wait_for_url(lambda u: JA_DOMAIN not in u, timeout=15000)
            return True
        except Exception:
            pass
        # 二次验证(短信/交我办):脚本不代填,给人 120 秒在窗口里完成
        if page.locator("#input-login-sms-code, #input-bind-sms-code").count():
            print("[login] 触发二次验证,请在浏览器窗口完成(120 秒内)")
            try:
                page.wait_for_url(lambda u: JA_DOMAIN not in u, timeout=120000)
                return True
            except Exception:
                return False
        print(f"[login] 第 {attempt} 次未通过(多半是验证码错),重试")
        page.goto(JA_LOGIN_ENTRY, wait_until="domcontentloaded")
    return JA_DOMAIN not in page.url


# ── 教务课表 ──────────────────────────────────────────────────────────────

def auto_year_term(today: dt.date | None = None) -> tuple[str, str]:
    today = today or dt.date.today()
    if today.month >= 9 or today.month == 1:
        year = today.year if today.month >= 9 else today.year - 1
        return str(year), "3"      # 秋季
    return str(today.year - 1), "12"  # 春季


def fetch_schedule(cookies: dict[str, str], year: str, term: str) -> list[dict]:
    r = requests.post(KB_URL, data={"xnm": year, "xqm": term}, cookies=cookies,
                      headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    r.raise_for_status()
    data = r.json()
    if "kbList" not in data:
        raise RuntimeError("教务接口未返回 kbList:登录态无效或学年学期不对")
    out = []
    for k in data["kbList"]:
        slot = re.findall(r"\d+", k.get("jcs", ""))
        out.append({
            "name": k.get("kcmc", ""),
            "teacher": k.get("xm", ""),
            "location": k.get("cdmc", ""),
            "day": int(k.get("xqj") or 0),
            "slot_start": int(slot[0]) if slot else None,
            "slot_end": int(slot[-1]) if slot else None,
            "weeks": expand_weeks(k.get("zcd", "")),
            "week_str": k.get("zcd", ""),
        })
    return out


def expand_weeks(zcd: str) -> list[int]:
    """'1-16周' / '1-8周,10-16周' / '2-16周(双)' → 周次列表。"""
    weeks: set[int] = set()
    for part in zcd.split(","):
        m = re.search(r"(\d+)(?:-(\d+))?", part)
        if not m:
            continue
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        rng = range(a, b + 1)
        if "单" in part:
            rng = [w for w in rng if w % 2 == 1]
        elif "双" in part:
            rng = [w for w in rng if w % 2 == 0]
        weeks.update(rng)
    return sorted(weeks)


# ── Canvas 令牌(尽力而为)──────────────────────────────────────────────────

def try_create_canvas_token(page: Page) -> str | None:
    """走 Canvas 标准设置页新建访问许可证。选择器按 Canvas 通用版本写,未在交大实例核过。"""
    try:
        page.goto(f"{CANVAS_BASE}/login/openid_connect", wait_until="domcontentloaded")
        page.wait_for_url(lambda u: u.startswith(CANVAS_BASE) and "login" not in u, timeout=60000)
        page.goto(f"{CANVAS_BASE}/profile/settings", wait_until="domcontentloaded")
        page.click("a.add_access_token_link", timeout=10000)
        page.fill("#access_token_purpose", "dengjie-sync")
        page.click("#access_token_form button[type=submit]")
        tok = page.locator("#token_details_dialog .visible_token").first
        tok.wait_for(timeout=15000)
        token = tok.inner_text().strip()
        return token or None
    except Exception as e:
        print(f"[canvas] 自动生成令牌失败({e});请到 Canvas → 账户 → 设置 → 新建访问许可证 手动生成后用 --canvas-token 传入")
        return None


def verify_canvas_token(token: str) -> bool:
    r = requests.get(f"{CANVAS_BASE}/api/v1/users/self", headers={"Authorization": f"Bearer {token}"}, timeout=15)
    return r.status_code == 200


# ── 主流程 ───────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True, help="jAccount 用户名")
    ap.add_argument("--backend", help="后端地址,如 http://localhost:8000;不给则只写本地文件")
    ap.add_argument("--canvas-token", help="已有 Canvas 令牌;给了就不再尝试自动生成(MVP 只对演示账号用)")
    ap.add_argument("--web-user", help="登阶网页账号;给 --backend 时用于换取上传令牌")
    ap.add_argument("--year"); ap.add_argument("--term", choices=["3", "12"])
    ap.add_argument("--headless", action="store_true", help="无头模式;遇二次验证会失败,默认有头")
    args = ap.parse_args()
    password = getpass.getpass("jAccount 密码(不回显,不落盘): ")

    year, term = args.year or auto_year_term()[0], args.term or auto_year_term()[1]
    result: dict = {"synced_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "year": year, "term": term}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=args.headless)
        ctx = browser.new_context()
        page = ctx.new_page()
        if not jaccount_login(page, args.user, password):
            print("[login] jAccount 登录失败"); return 2
        cookies = {c["name"]: c["value"] for c in ctx.cookies() if "i.sjtu.edu.cn" in c.get("domain", "")}
        result["schedule"] = fetch_schedule(cookies, year, term)
        print(f"[schedule] 取到 {len(result['schedule'])} 门课程条目")

        token = args.canvas_token or try_create_canvas_token(page)
        if token and verify_canvas_token(token):
            result["canvas_token"] = token
            print("[canvas] 令牌可用")
        else:
            print("[canvas] 无可用令牌,跳过")
        browser.close()

    with open("sync_result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("[done] 已写 sync_result.json")

    if args.backend:
        # 先用网页账号登录后端换取短期上传令牌,上传因此归属该账号(同学期重复上传由后端幂等覆盖)
        base = args.backend.rstrip("/")
        web_user = args.web_user or input("登阶网页账号: ").strip()
        web_pass = getpass.getpass("登阶网页密码(不回显): ")
        lr = requests.post(f"{base}/api/login", json={"username": web_user, "password": web_pass}, timeout=15)
        if not lr.ok:
            print(f"[upload] 后端登录失败 {lr.status_code}"); return 3
        upload_token = lr.json().get("upload_token") or lr.json().get("token")
        r = requests.post(f"{base}/api/sync", json=result,
                          headers={"Authorization": f"Bearer {upload_token}"}, timeout=30)
        print(f"[upload] {r.status_code} {r.text[:200]}")
        return 0 if r.ok else 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
