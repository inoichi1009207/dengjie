# 登阶 · 本地 MVP

让长期目标走进交大学生的每一周:AI 拆目标 → 任务池 → 周视图与本周容量缺口 → 每日复盘 → 小组共进。

## 跑起来

```bash
pip install -r requirements.txt
copy .env.example .env      # 填 DEEPSEEK_API_KEY;不填也能跑(桩模式)
run.bat                     # 或:uvicorn app.main:app --reload --port 8000
```

打开 http://localhost:8000 ,注册一个账号即可。

## 环境变量

| 变量 | 作用 |
|---|---|
| `DEEPSEEK_API_KEY`(或 `LLM_API_KEY`) | DeepSeek 官方接口;缺省走桩,拆解与课表解析用固定规则 |
| `LLM_BASE_URL` / `LLM_MODEL` | 默认 `https://api.deepseek.com` / `deepseek-chat` |
| `DEMO_CANVAS_TOKEN` | 演示账号的 Canvas 令牌(操作员预置,用户也可在设置页填自己的) |
| `DENGJIE_DB` | SQLite 文件路径,默认项目根 `dengjie.db` |
| `DENGJIE_TODAY` | 测试/演示用「今天」,格式 YYYY-MM-DD |

## 课业怎么进来

1. **课表 PDF / 文字**:接入页上传教学信息网导出的 PDF(浏览器内 pdf.js 抽文本,服务器不必装依赖),或粘贴课表文字,解析后核对保存。
2. **一键导入**:接入页「复制提示词」,交给你自己的 AI 助手(Claude Code / Codex / sjtu-agent),它按 `tools/PROMPT.md` 的格式回一段 JSON(课表 + Canvas 作业 + 邮箱待办),粘回导入框。
3. **服务端直连**:接入页填 Canvas 令牌 / 邮箱账号,由服务器拉取(演示账号)。

`tools/dengjie_sync.py` 是早期的本地同步器,已不在产品路径上,留作参考。

## 结构

```
app/main.py    FastAPI 路由(鉴权、目标、任务、课表、周视图、复盘、Canvas、小组)
app/rules.py   纯规则:周界、容量缺口、今日清单、「太累了」重排、连续天数
app/llm.py     DeepSeek 调用 + 桩
app/canvas.py  Canvas 待办拉取
app/db.py      SQLite
app/static/    纯 HTML/JS 前端
tools/         同步器与提示词
tests/         python -m unittest
```

## 测试

```bash
python -m unittest -q
```

## 部署给评委看(公网网址)

代码就是一个普通的 FastAPI 服务,任何能跑 Docker 或 Python 的地方都行。两条现成路:

**A. Render(免费,十分钟)**
1. 把 `D:\dengjie` 推到 GitHub 仓库。
2. Render 控制台 → New → Blueprint → 选该仓库,它会读 `render.yaml`。
3. 在环境变量里填 `DEEPSEEK_API_KEY`(以及可选的 `DEMO_CANVAS_TOKEN`、`DEMO_MAIL_USER`、`DEMO_MAIL_PASS`)。
4. 部署完成得到 `https://dengjie.onrender.com` 一类的网址。免费实例文件系统不持久,重新部署后数据清空;路演前别重新部署即可。

**B. 云服务器(阿里云 / 腾讯云 学生机)**
```bash
git clone <仓库> && cd dengjie
pip install -r requirements.txt
echo "DEEPSEEK_API_KEY=..." > .env
DENGJIE_DB=/srv/dengjie.db nohup python -m uvicorn app.main:app --host 0.0.0.0 --port 80 &
```
数据持久在 `/srv/dengjie.db`。域名与 HTTPS 可选,评委用 IP 也能访问。

两条路 `apikey.txt` 都不上传(已 gitignore),密钥只走环境变量。

> 安全提示:用户在设置页填的 Canvas 令牌与邮箱密码目前以明文存于 SQLite `users` 表(演示阶段设计)。请只用团队演示账号,不要接入真实个人凭据;正式版计划 Canvas 走 OAuth、邮箱不接真实用户。
