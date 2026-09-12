# 登阶 · 一键导入提示词

把下面「---」之后的整段交给你自己在用的 AI 助手(Claude Code、Codex、sjtu-agent、任何能替你操作浏览器或调接口的助手)。它会替你取课表、Canvas 作业、邮箱里的待办,整理成一段 JSON;你把 JSON 粘贴到登阶「接入 → 一键导入」框里即可。密码只经过你自己的工具,不经过登阶。

三部分各自独立:助手做不到哪一部分,就把那一部分留空数组,其余照常。

---

请帮我整理本学期的课业信息,最终**只输出一段 JSON**(不要解释文字),格式如下:

```json
{
  "schedule": [
    {"name": "课程名", "teacher": "教师或 null", "location": "地点或 null", "day": 1, "slot_start": 3, "slot_end": 4, "weeks": [1, 2, 3]}
  ],
  "canvas_tasks": [
    {"external_id": "canvas:assignment:12345", "title": "[课程名] 作业标题", "due": "2026-09-30", "est_hours": 2}
  ],
  "mail_tasks": [
    {"external_id": "mail:<邮件唯一标识>", "title": "一句话说清要做什么", "due": "2026-09-25 或 null", "est_hours": 2}
  ]
}
```

## 第一部分:课表(教学信息网)

1. 打开 https://i.sjtu.edu.cn/jaccountlogin ,用我的 jAccount 登录(验证码由我输入)。
2. 登录后请求 `POST https://i.sjtu.edu.cn/kbcx/xskbcx_cxXsKb.html`,表单 `xnm=<学年,如 2026>`、`xqm=<3 表示秋季 / 12 表示春季>`,取响应里的 `kbList`。
3. 字段对应:`kcmc`→name,`xm`→teacher,`cdmc`→location,`xqj`→day(1=周一 … 7=周日),`jcs` 如「3-4」→slot_start 3 / slot_end 4,`zcd` 如「1-16周」展开成周次列表(「单」只取奇数周,「双」只取偶数周)。
4. 如果你拿不到接口,也可以让我把课表页面文字或 PDF 给你,你按同样格式整理。

## 第二部分:Canvas 作业

1. 打开 https://oc.sjtu.edu.cn ,用 jAccount 登录后,请求 `GET /api/v1/users/self/todo` 与 `GET /api/v1/planner/items?per_page=50`(用浏览器会话即可;如需令牌,在 账户 → 设置 → 新建访问许可证 生成,用完即删)。
2. 只取**尚未提交、未过期**的作业。`external_id` 用 `canvas:assignment:<assignment id>`,title 前面加 `[课程名]`,due 取 `due_at` 的日期部分。

## 第三部分:邮箱待办

1. 用 IMAP 读 `mail.sjtu.edu.cn:993`(账号 = jAccount 用户名@sjtu.edu.cn,密码 = jAccount 密码),只看最近 7 天的收件箱。
2. 只抽**需要我动手做的事**(作业、报告、报名、提交、考试准备等),纯通知不要。每封最多一条;`external_id` 用 `mail:` 加邮件 UID 或主题前 40 字;due 只在邮件里有明确日期时填。
3. **不要把邮件正文、邮箱地址、电话写进 JSON**,title 用你自己的话概括。

做不到的部分留空数组。最后只输出那段 JSON。

## 第四部分:成绩(可选,给目标设定做参考)

登录教学信息网后打开「成绩查询」,把已出分课程整理为:

```json
"grades": [{"course": "课程名", "credit": 3, "score": 88, "term": "2025-2026-1"}]
```

放进同一段 JSON 里(与 schedule、canvas_tasks、mail_tasks 并列)。拿不到就留空数组。
