// 登阶前端:纯 fetch,无框架。
const $ = (s) => document.querySelector(s);
const el = (tag, attrs = {}, ...kids) => { const n = document.createElement(tag); for (const [k, v] of Object.entries(attrs)) { if (k === "class") n.className = v; else if (k.startsWith("on")) n.addEventListener(k.slice(2), v); else if (v !== null && v !== undefined) n.setAttribute(k, v); } for (const c of kids) if (c !== null && c !== undefined && c !== "") n.append(c); return n; };
async function api(path, method = "GET", body) {
  let r;
  try { r = await fetch(path, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined }); }
  catch (e) { toastSafe("网络不通:" + e.message, true); throw e; }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) {
    if (r.status === 401 && path !== "/api/me") { location.reload(); }
    const msg = typeof j.detail === "string" ? j.detail : (Array.isArray(j.detail) ? j.detail.map(d => d.msg).join(";") : (r.status >= 500 ? "服务器出错了,请重试" : r.statusText));
    toastSafe(msg, true); throw new Error(msg);
  }
  return j;
}
function toastSafe(msg, warn) { try { toast(msg, warn); } catch { /* toast 未就绪 */ } }
const SRC = { ai: "AI 拆解", manual: "手动", canvas: "Canvas", email: "邮件" };
const fmtDate = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const todayStr = () => fmtDate(new Date());
let ME = null, CAL = { y: null, m: null }, GOAL_TITLES = {}, CAL_MODE = "week", WEEK_OFF = 0;
// 分类色:有目标的任务按目标编号取色;没有目标的按来源;课程按课名散列
const PAL = ["var(--c0)", "var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)", "var(--c5)", "var(--c6)", "var(--c7)"];
const hashStr = (s) => { let h = 0; for (const ch of String(s)) h = (h * 31 + ch.charCodeAt(0)) >>> 0; return h; };
const colorOf = (t) => t.goal_id ? PAL[t.goal_id % PAL.length] : `var(--${t.source || "manual"})`;
const CLASS_HINT = [["实践", "var(--c6)"], ["实验", "var(--c0)"], ["体育", "var(--c4)"], ["英语", "var(--c5)"], ["数学", "var(--c2)"], ["物理", "var(--c7)"], ["程序", "var(--c3)"], ["代数", "var(--c1)"]];
const colorOfClass = (s) => { const h = CLASS_HINT.find(([k]) => s.name.includes(k)); return h ? h[1] : PAL[hashStr(s.name) % PAL.length]; };
const goalColor = (gid) => PAL[gid % PAL.length];

// ── 登录 / 壳 ──
async function boot() { try { ME = await api("/api/me"); showApp(); } catch { ME = null; $("#landing").hidden = false; } }
async function auth(path) {
  try { await api(path, "POST", { username: $("#au").value.trim(), password: $("#ap").value }); ME = await api("/api/me"); showApp(); }
  catch (e) { $("#auth-msg").textContent = e.message; }
}
$("#login").onclick = () => auth("/api/login");
$("#register").onclick = () => auth("/api/register");
$("#logout").onclick = async () => { await api("/api/logout", "POST"); location.reload(); };
function showApp() { $("#landing").hidden = true; $("#app").hidden = false; $("#who").textContent = ME.username; $("#who2").textContent = ME.username; switchTab("today"); }
$("#logout2").onclick = async () => { await api("/api/logout", "POST"); location.reload(); };
document.querySelectorAll("#nav button[data-tab]").forEach(b => b.onclick = () => switchTab(b.dataset.tab));
function switchTab(t) {
  document.querySelectorAll(".tab").forEach(s => s.hidden = true); $(`#tab-${t}`).hidden = false;
  document.querySelectorAll("#nav button").forEach(b => b.classList.toggle("active", b.dataset.tab === t));
  ({ today: loadToday, goals: loadGoals, calendar: loadCalendar, ddl: loadDdl, integrations: loadIntegrations, groups: loadGroups, settings: loadSettings })[t]();
}
document.querySelectorAll("[data-go]").forEach(b => b.onclick = () => switchTab(b.dataset.go));
$("#demo-seed").onclick = async () => { if (!confirm("会先清空你当前的个人数据,再灌入演示数据。继续?")) return; const r = await api("/api/demo/seed", "POST"); $("#demo-msg").textContent = `已灌入,本周可投入 ${r.weekly_hours} 小时。`; };
$("#demo-reset").onclick = async () => { if (!confirm("清空目标、任务、课表、复盘记录?")) return; await api("/api/demo/reset", "POST"); $("#demo-msg").textContent = "已清空。"; };
$("#ddl-open").onclick = () => switchTab("ddl");
$("#ddl-back").onclick = () => switchTab("today");
const daysLabel = (n) => n < 0 ? `逾期 ${-n} 天` : n === 0 ? "今天" : `${n} 天`;
const cdClass = (n) => n < 0 ? "countdown over" : n <= 3 ? "countdown soon" : "countdown";

// ── DDL ──
async function loadDdl() {
  const d = await api("/api/ddl");
  const due = $("#ddl-due"); due.innerHTML = ""; due.hidden = !d.due_goals.length; due.className = "card due-card";
  if (d.due_goals.length) {
    due.append(el("h2", {}, "到期的目标,结个账"));
    for (const g of d.due_goals) {
      const ni = el("input", { type: "date" });
      if (g.can_settle === false) { due.append(el("div", { class: "row" }, el("span", { class: cdClass(g.days_left) }, daysLabel(g.days_left)), el("b", {}, g.title), el("span", { class: "muted" }, "共同目标,由组长结算"))); continue; }
      due.append(el("div", { class: "row" }, el("span", { class: cdClass(g.days_left) }, daysLabel(g.days_left)), el("b", {}, g.title), el("span", { class: "muted" }, `任务 ${g.tasks_done}/${g.tasks_total}`),
        el("button", { class: "small primary", onclick: async () => { await api(`/api/goals/${g.id}/settle`, "POST", { action: "achieved" }); loadDdl(); } }, "达成了"),
        ni, el("button", { class: "small", onclick: async () => { if (!ni.value) return alert("先选新截止日"); await api(`/api/goals/${g.id}/settle`, "POST", { action: "extend", new_due: ni.value }); loadDdl(); } }, "延期到"),
        el("button", { class: "small danger", onclick: async () => { if (confirm("放弃这个目标?未完成的任务会删掉")) { await api(`/api/goals/${g.id}/settle`, "POST", { action: "drop" }); loadDdl(); } } }, "放弃")));
    }
  }
  const gl = $("#ddl-goals"); gl.innerHTML = "";
  if (!d.upcoming_goals.length) gl.append(el("li", { class: "empty" }, "没有带截止日的目标。"));
  for (const g of d.upcoming_goals) gl.append(el("li", {}, el("span", { class: cdClass(g.days_left) }, daysLabel(g.days_left)), el("div", { class: "t" }, g.title, g.group_id ? el("span", { class: "tag" }, "共同目标") : "", el("div", { class: "meta" }, `⌛ ${g.due} · 任务 ${g.tasks_done}/${g.tasks_total}`))));
  const tl = $("#ddl-tasks"); tl.innerHTML = "";
  if (!d.tasks.length) tl.append(el("li", { class: "empty" }, "没有带截止日的任务。"));
  for (const t of d.tasks) tl.append(el("li", {}, el("span", { class: cdClass(t.days_left) }, daysLabel(t.days_left)), el("div", { class: "t" }, t.title, el("span", { class: `tag ${t.source}` }, SRC[t.source] || t.source), el("div", { class: "meta" }, `⌛ ${t.due} · ⏰ ${t.remaining_hours}h`)),
    el("button", { class: "small primary", onclick: async () => { await api(`/api/review/${t.id}/done`, "POST"); loadDdl(); } }, "完成")));
}

// ── 时间账(人话) ──
function ledgerCard(box, w) {
  const g = w.gap, over = g.gap > 0, spare = Math.abs(g.gap);
  const pct = g.weekly_hours > 0 ? Math.min(100, g.committed_hours / g.weekly_hours * 100) : (g.committed_hours > 0 ? 100 : 0);
  box.className = "card ledger-card " + (over ? "over" : "ok"); box.innerHTML = "";
  box.append(
    el("div", {},
      el("p", { class: "headline" }, over ? `这周排多了 ${spare} 小时` : `这周还有 ${spare} 小时空余`),
      el("p", { class: "sub" }, over ? "已经排下的事比能投入的时间多,建议推后或删减几件。" : `已排 ${g.committed_hours} 小时,可投入 ${g.weekly_hours} 小时。`),
      el("div", { class: "bar" }, el("i", { style: `width:${pct}%` }))),
    el("div", { class: "facts" },
      el("div", {}, "本周已排", el("b", {}, `${g.committed_hours} h`), el("button", { class: "small ghost", title: "按截止日与每天容量把任务摊到各天", onclick: async () => { const r = await api("/api/plan/auto", "POST"); alert(`已排 ${r.planned} 件任务到各天${r.overflow.length ? `;${r.overflow.length} 件截止前排不下:${r.overflow.map(o => o.title).join("、")}` : ""}`); location.hash = ""; switchTab(document.querySelector("#nav button.active").dataset.tab); } }, "自动排程")),
      el("div", {}, "本周可投入任务", el("b", {}, `${g.weekly_hours} h`), el("span", {}, `日承载力 10 h × 7${w.class_hours != null ? " − 课时 " + w.class_hours + " h" : ""} `), el("button", { class: "small ghost", title: "日承载力 10 × 7 − 本周课时", onclick: async () => { const r = await api("/api/settings/adopt_suggested", "POST"); toast(`已按课表重算:本周可投入 ${r.weekly_hours} 小时`); switchTab(document.querySelector("#nav button.active").dataset.tab); } }, "按课表重算")),
      el("div", {}, "教学周", el("b", {}, w.week_no ? `第 ${w.week_no} 周` : (ME && ME.semester_start ? "开学前" : "未设")))));
}

// ── 今日 ──
function taskLi(t, actions) {
  const meta = [t.due ? `⌛ ${t.due}` : "", t.scheduled_date && t.scheduled_date !== t.due ? `📅 ${t.scheduled_date}` : "", `⏰ ${t.remaining_hours}h`].filter(Boolean).join(" · ");
  const overdue = t.status !== "done" && t.due && t.due < todayStr();
  return el("li", { style: `--tc:${colorOf(t)}` }, el("div", { class: "t" }, t.title, el("span", { class: `tag ${t.source}` }, SRC[t.source] || t.source), overdue ? el("span", { class: "tag overdue" }, "逾期") : "", t.postponed ? el("span", { class: "tag manual" }, `已推迟 ${t.postponed} 次`) : "", el("div", { class: "meta" }, t.note ? `${meta} · ${t.note}` : meta)), ...actions);
}
function toast(msg, warn) { const t = $("#toast"); t.textContent = msg; t.className = "toast" + (warn ? " warn" : ""); t.hidden = false; clearTimeout(toast._h); toast._h = setTimeout(() => t.hidden = true, 6000); }
const fmtH = (h) => { const hh = Math.floor(h), mm = Math.round((h - hh) * 60); return `${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}`; };
// 负载条:每件事按小时占一块,不引入时刻。cap = 日承载力,超出部分照样画,只是标红提示。
function renderStrip(box, classes, tasks, cap) {
  // 日承载力 = 课 + 任务共用;做完的任务仍占位(时间已经花掉)
  box.innerHTML = "";
  const clsItems = classes.map(s => ({ kind: "class", title: s.name, hours: Math.max(0.25, ((s.slot_end || s.slot_start || 1) - (s.slot_start || 1) + 1) * 0.75), color: colorOfClass(s) }));
  const tkItems = tasks.map(t => ({ kind: t.status === "done" ? "done" : "task", title: t.title, hours: Math.max(0.25, +(t.status === "done" ? t.est_hours : t.remaining_hours) || 0.5), color: colorOf(t) }));
  const cls = clsItems.reduce((a, b) => a + b.hours, 0), tk = tkItems.reduce((a, b) => a + b.hours, 0), used = cls + tk;
  const capN = cap || 10, total = Math.max(capN, used);
  const bar = el("div", { class: "strip" });
  for (const it of [...clsItems, ...tkItems]) bar.append(el("div", { class: `seg ${it.kind}`, style: `width:${it.hours / total * 100}%;--tc:${it.color}`, title: `${it.title} · ${it.hours}h` }, it.title));
  if (used < total) bar.append(el("div", { class: "free" }, `空余 ${(total - used).toFixed(1)}h`));
  box.append(bar);
  const over = used > capN;
  const doneH = tkItems.filter(i => i.kind === "done").reduce((a, b) => a + b.hours, 0), todoH = tk - doneH;
  box.append(el("div", { class: "strip-meta", style: over ? "color:var(--red)" : "" }, `已完成 ${doneH.toFixed(1)}h · 待做 ${todoH.toFixed(1)}h · 课 ${cls.toFixed(1)}h · ${over ? "超出 " + (used - capN).toFixed(1) + "h" : "空余 " + (capN - used).toFixed(1) + "h"} / 日承载力 ${capN.toFixed(0)}h`));
  const legend = el("div", { class: "strip-legend" });
  const seen = new Set();
  for (const t of tasks) { const k = t.goal_id ? ((GOAL_TITLES[t.goal_id] || `目标 ${t.goal_id}`).slice(0, 14)) : (SRC[t.source] || t.source); if (seen.has(k)) continue; seen.add(k); legend.append(el("span", { style: `--tc:${colorOf(t)}` }, el("i"), k)); }
  if (classes.length) legend.append(el("span", { style: "--tc:var(--green)" }, el("i"), "课(斜纹)"));
  if (tasks.some(t => t.status === "done")) legend.append(el("span", { class: "muted" }, "划线 = 已完成,时间已花掉"));
  box.append(legend);
}
// 行内改任务:标题 / 预计小时 / 截止日
function editTask(li, t, refresh) {
  if (li.classList.contains("editing")) return;
  li.classList.add("editing");
  const ti = el("input", { value: t.title }), hi = el("input", { type: "number", step: "0.5", min: "0", value: t.est_hours }), di = el("input", { type: "date", value: t.due || "" });
  const row = el("div", { class: "edit-row" }, ti, hi, di,
    el("button", { class: "small primary", onclick: async () => { await api(`/api/tasks/${t.id}`, "PATCH", { title: ti.value.trim() || t.title, est_hours: +hi.value || t.est_hours, due: di.value || null }); refresh(); } }, "保存"),
    el("button", { class: "small", onclick: () => { row.remove(); li.classList.remove("editing"); } }, "取消"));
  li.prepend(row);
}
const editBtn = (t, refresh) => el("button", { class: "small", onclick: (e) => editTask(e.target.closest("li"), t, refresh) }, "改");
const classLi = (s) => el("li", { class: "cls", style: `--tc:${colorOfClass(s)}` }, el("div", { class: "t" }, `${s.slot_start}-${s.slot_end} 节 · ${s.name}`, el("div", { class: "meta" }, [s.location, s.teacher].filter(Boolean).join(" · "))));
async function loadToday() {
  const [d, w] = await Promise.all([api("/api/today"), api("/api/week")]);
  GOAL_TITLES = d.goal_titles || {};
  $("#today-date").textContent = d.date; $("#streak").textContent = d.streak;
  const ob = $("#onboarding"); const need = d.onboarding && (!d.onboarding.has_goal || !d.onboarding.has_schedule);
  ob.hidden = !need;
  if (need) { ob.innerHTML = ""; ob.append(el("h2", {}, "三步开始登阶"), el("div", { class: "onboard-steps" },
    el("div", {}, el("b", {}, "① 立一个长期目标"), el("span", { class: "hint" }, d.onboarding.has_goal ? "已完成" : "说一句话,AI 拆成任务"), " ", el("button", { class: "small primary", onclick: () => switchTab("goals") }, d.onboarding.has_goal ? "再立一个" : "去立目标")),
    el("div", {}, el("b", {}, "② 导入课表"), el("span", { class: "hint" }, d.onboarding.has_schedule ? "已导入" : "上传 PDF / 截图 / 文字,可投入时长自动算"), " ", el("button", { class: "small primary", onclick: () => switchTab("integrations") }, d.onboarding.has_schedule ? "重新导入" : "去导入")),
    el("div", {}, el("b", {}, "③ 拉作业、读邮件"), el("span", { class: "hint" }, "进待确认区,你点计入才算"), " ", el("button", { class: "small", onclick: () => switchTab("integrations") }, "去接入")))); }
  ledgerCard($("#ledger-card"), w);
  const cl = $("#today-classes"); cl.innerHTML = ""; $("#today-week").textContent = d.week_no ? `第 ${d.week_no} 教学周` : "";
  if (!d.classes.length) cl.append(el("li", { class: "empty" }, "今天没课。"));
  for (const s of d.classes) cl.append(classLi(s));
  const ul = $("#today-list"); ul.innerHTML = "";
  if (!d.tasks.length) ul.append(el("li", { class: "empty" }, "今天没有安排。在下面直接加一件,或去「目标」立一个。"));
  for (const t of d.tasks) ul.append(taskLi(t, [
    el("button", { class: "small primary", onclick: async () => { await api(`/api/review/${t.id}/done`, "POST"); loadToday(); } }, "完成"),
    editBtn(t, loadToday),
    el("button", { class: "small warn", onclick: async () => {
      const r = await api(`/api/review/${t.id}/postpone`, "POST");
      let msg = `「${t.title}」挪到明天 ${r.scheduled_date}(第 ${r.postponed} 次)`;
      if (r.past_due) msg += `;已越过它自己的截止日 ${t.due}`;
      if (r.goal_risk) msg += `;目标「${r.goal}」截止 ${r.goal_due},再推就来不及了`;
      toast(msg, r.past_due || r.goal_risk); loadToday();
    } }, "太累了"),
    (() => { const di = el("input", { type: "date", style: "width:140px" }); return el("span", { class: "row", style: "margin:0;gap:4px" }, el("button", { class: "small warn", onclick: async () => { if (!di.value) { di.focus(); return; } await api(`/api/tasks/${t.id}`, "PATCH", { scheduled_date: di.value }); toast(`「${t.title}」推迟到 ${di.value}`, di.value > (t.due || "9999")); loadToday(); } }, "推迟到"), di); })(),
  ]));
  const cap = d.daily_cap || 10; $("#daily-cap").textContent = cap.toFixed(0);
  const doneList = d.done_today || [];
  renderStrip($("#today-strip"), d.classes || [], [...d.tasks, ...doneList], cap);
  const up = $("#today-upcoming"); up.innerHTML = "";
  const soon = (await api("/api/ddl")).tasks.filter(t => t.days_left <= 7).slice(0, 8);
  if (!soon.length) up.append(el("li", { class: "empty" }, "七天内没有到期的任务。"));
  for (const t of soon) up.append(el("li", { style: `--tc:${colorOf(t)}` }, el("span", { class: cdClass(t.days_left) }, daysLabel(t.days_left)), el("div", { class: "t" }, t.title, el("div", { class: "meta" }, `⌛ ${t.due} · ⏰ ${t.remaining_hours}h`))));
  // 余力:今天曾有任务且全部做完
  const sp = $("#spare-card"); const doneToday = doneList.length;
  sp.hidden = !(d.tasks.length === 0 && doneToday > 0);
  if (!sp.hidden) { sp.innerHTML = ""; sp.append(el("p", {}, el("b", {}, `今天的 ${doneToday} 件都做完了,还有余力?`)), el("div", { class: "row" },
    el("button", { class: "small primary", onclick: async () => { try { const r = await api("/api/review/pull_tomorrow", "POST"); toast(`已把「${r.pulled}」拉到今天`); } catch (e) { toast(e.message, true); } loadToday(); } }, "把明天的一件拉到今天"),
    el("button", { class: "small", onclick: async () => { const r = await api("/api/settings/boost_weekly", "POST"); toast(`本周可投入上调为 ${r.weekly_hours} 小时(日承载力 ${(r.weekly_hours / 7).toFixed(1)})`); loadToday(); } }, "承载力上调一成"))); }
  $("#calib-panel").hidden = true;
  loadDailyReview().catch(() => {});
  const bn = $("#pending-banner"); bn.hidden = !d.pending.length;
  if (d.pending.length) { bn.innerHTML = ""; bn.append(el("span", {}, el("b", {}, `${d.pending.length} 项`), " 从 Canvas / 邮件导入的事项等你确认,确认后才算进这周的账。"), el("button", { class: "small primary", onclick: () => switchTab("integrations") }, "去接入页确认")); }
  updateBadge(d.pending.length);
}
function updateBadge(n) { const b = $("#nav-pending"); b.hidden = !n; b.textContent = n; }
function renderPending(ul, pending, refresh) {
  ul.innerHTML = "";
  if (!pending.length) ul.append(el("li", { class: "empty" }, "暂无。去「接入」拉取 Canvas 作业、读取邮件,或在「设置」载入演示数据。"));
  for (const t of pending) ul.append(taskLi(t, [
    el("button", { class: "small primary", onclick: async () => { await api(`/api/tasks/${t.id}/confirm`, "POST"); refresh(); } }, "计入"),
    el("button", { class: "small danger", onclick: async () => { await api(`/api/tasks/${t.id}`, "DELETE"); refresh(); } }, "忽略"),
  ]));
  if (pending.length > 1) ul.append(el("li", {}, el("button", { class: "small ghost", onclick: async () => { for (const t of pending) await api(`/api/tasks/${t.id}/confirm`, "POST"); refresh(); } }, `全部计入(${pending.length})`)));
}
$("#quick-add").onsubmit = async (e) => {
  e.preventDefault();
  const due = $("#qa-due").value || todayStr();
  await api("/api/tasks", "POST", { title: $("#qa-title").value.trim(), est_hours: +$("#qa-hours").value || 1, due, scheduled_date: due });
  $("#qa-title").value = ""; loadToday();
};
let MOOD = null;
document.querySelectorAll("button.mood").forEach(b => b.onclick = () => { MOOD = b.dataset.mood; document.querySelectorAll("button.mood").forEach(x => x.classList.toggle("active", x === b)); });
async function loadDailyReview() {
  const r = await api("/api/review/daily"); const st = r.stats; const box = $("#review-stats"); box.innerHTML = "";
  box.append(el("div", {}, "完成", el("b", {}, `${st.done} 件`), `${st.done_hours}h`), el("div", {}, "推迟", el("b", {}, `${st.postponed} 件`)), el("div", {}, "还剩", el("b", {}, `${st.left} 件`)), el("div", {}, "连续", el("b", {}, `${st.streak} 天`)));
  if (r.review) { $("#review-note").value = r.review.note || ""; MOOD = r.review.mood; document.querySelectorAll("button.mood").forEach(x => x.classList.toggle("active", x.dataset.mood === MOOD)); if (r.review.ai_comment) { $("#review-ai").textContent = "AI:" + r.review.ai_comment; $("#review-ai").hidden = false; } }
  else { $("#review-ai").hidden = true; }
}
$("#review-save").onclick = async () => {
  $("#review-msg").textContent = "记录中…";
  try {
    const r = await api("/api/review/daily", "POST", { note: $("#review-note").value, mood: MOOD });
    $("#review-msg").textContent = "已记下"; $("#review-ai").textContent = "AI:" + r.review.ai_comment; $("#review-ai").hidden = false;
    // 记完让用户自己填新的承载力;顺带看明天排了多少
    const [me, w] = await Promise.all([api("/api/me"), api("/api/week")]);
    const tm = new Date(); tm.setDate(tm.getDate() + 1); const tmS = fmtDate(tm);
    const day = w.days.find(d => d.date === tmS);
    const tmCls = day ? day.slots.reduce((a, x) => a + ((x.slot_end || x.slot_start || 1) - (x.slot_start || 1) + 1) * 0.75, 0) : 0;
    const tmTk = day ? day.tasks.reduce((a, x) => a + (+x.remaining_hours || 0), 0) : 0;
    const box = $("#review-cap"); box.innerHTML = ""; box.hidden = false;
    const hi = el("input", { type: "number", step: "0.5", min: "0", max: "168", value: me.weekly_hours, style: "width:110px" });
    box.append(el("p", {}, el("b", {}, "明天的承载力?"), el("span", { class: "muted" }, ` 现在本周可投入 ${me.weekly_hours}h(日均 ${(me.weekly_hours / 7).toFixed(1)}h)`)),
      el("div", { class: "row" }, el("span", {}, "本周可投入改为"), hi, el("span", {}, "h"), el("button", { class: "small primary", onclick: async () => { await api("/api/settings", "PUT", { weekly_hours: +hi.value }); toast(`本周可投入已改为 ${hi.value}h`); loadToday(); } }, "就这样")));
    if (tmCls + tmTk > 10) box.append(el("p", { class: "hint", style: "color:var(--orange)" }, `明天已排 课 ${tmCls.toFixed(1)}h + 任务 ${tmTk.toFixed(1)}h,超过日承载力 10h。`, " ", el("button", { class: "small warn", onclick: () => switchTab("calendar") }, "去日历腾挪")));
  } catch (e) { $("#review-msg").textContent = e.message; }
};
$("#calib-toggle").onclick = async () => {
  const p = $("#calib-panel"); if (!p.hidden) { p.hidden = true; return; }
  const ts = (await api("/api/today")).tasks;
  p.innerHTML = ""; p.hidden = false;
  if (!ts.length) { p.append(el("p", { class: "hint" }, "今天没有待做任务。")); return; }
  const rows = ts.map(t => ({ t, inp: el("input", { type: "number", step: "0.5", min: "0", value: t.remaining_hours, style: "width:80px" }) }));
  const tbl = el("table", { class: "grid" }, el("thead", {}, el("tr", {}, el("th", {}, "任务"), el("th", {}, "截止"), el("th", {}, "原估时"), el("th", {}, "剩余小时"))));
  const tb = el("tbody"); for (const r of rows) tb.append(el("tr", {}, el("td", {}, r.t.title), el("td", {}, r.t.due || "—"), el("td", {}, `${r.t.est_hours}`), el("td", {}, r.inp))); tbl.append(tb); p.append(tbl);
  p.append(el("div", { class: "row" }, el("button", { class: "small primary", onclick: async () => {
    const items = rows.filter(r => +r.inp.value !== +r.t.remaining_hours).map(r => ({ id: r.t.id, remaining_hours: +r.inp.value }));
    const r = await api("/api/tasks_batch", "PATCH", { items }); toast(`已更新 ${r.updated} 条估时`); loadToday();
  } }, "保存全部"), el("button", { class: "small", onclick: () => p.hidden = true }, "收起")));
};
// ── 目标 ──
let PREVIEW = [];
function renderPreview() {
  const tb = $("#g-rows"); tb.innerHTML = "";
  PREVIEW.forEach((t, i) => tb.append(el("tr", {},
    el("td", {}, el("input", { value: t.title, oninput: e => t.title = e.target.value })),
    el("td", {}, el("input", { type: "number", step: "0.5", value: t.est_hours, oninput: e => t.est_hours = +e.target.value })),
    el("td", {}, el("input", { type: "date", value: t.due || "", oninput: e => t.due = e.target.value || null })),
    el("td", {}, el("input", { value: t.resource || "", oninput: e => t.resource = e.target.value || null }), t.resource_verified === true ? el("span", { class: "tag canvas", title: t.resource_url || "" }, "目录内") : t.resource_verified === false ? el("span", { class: "tag manual" }, "未核验") : ""),
    el("td", {}, el("button", { class: "small link", onclick: () => { PREVIEW.splice(i, 1); renderPreview(); } }, "删")))));
  $("#g-preview").hidden = false;
}
async function loadGrades() {
  const g = await api("/api/grades"); const box = $("#grades-card"); box.hidden = !g.count; if (!g.count) return;
  box.innerHTML = ""; box.append(el("h2", {}, "成绩参考 ", el("span", { class: "muted" }, "来自导入的成绩,定目标时看一眼")));
  box.append(el("div", { class: "row" }, el("span", { class: "pill" }, `GPA ${g.gpa ?? "—"}(4.3 制)`), el("span", { class: "pill" }, `均分 ${g.avg ?? "—"}`), el("span", { class: "muted" }, `${g.count} 门`)));
  if (g.weakest.length) box.append(el("p", { class: "hint" }, "分数最低的三门:", g.weakest.map(r => `${r.course} ${r.score}`).join(" · "), " —— 想补强就从这里立目标。"));
  const tbl = el("table", { class: "grid" }, el("thead", {}, el("tr", {}, el("th", {}, "课程"), el("th", {}, "学分"), el("th", {}, "成绩"), el("th", {}, "学期"))));
  const tb = el("tbody"); for (const r of g.grades) tb.append(el("tr", {}, el("td", {}, r.course), el("td", {}, r.credit ?? ""), el("td", {}, r.score ?? ""), el("td", {}, r.term || ""))); tbl.append(tb); box.append(tbl);
}
let HISTORY = [];   // 与 AI 的讨论记录,前端持有
const tasksAsText = (ts) => ts.map((t, i) => `${i + 1}. ${t.title}(${t.est_hours}h,截止 ${t.due || "未定"})`).join("\n");
$("#g-decompose").onclick = async () => {
  if (!$("#g-title").value.trim()) { $("#g-msg").textContent = "先写下目标"; return; }
  $("#g-msg").textContent = "AI 拆解中,几秒钟…"; $("#g-decompose").disabled = true;
  try {
    const r = await api("/api/goals/decompose", "POST", { title: $("#g-title").value.trim(), due: $("#g-due").value || null });
    PREVIEW = r.tasks; HISTORY = [{ role: "assistant", content: tasksAsText(r.tasks) }]; renderPreview(); $("#g-note").textContent = "";
    const ctx = (r.context || []).map(c => `${c.term} → ${c.title}`).join(";");
    $("#g-msg").textContent = (r.llm === "stub" ? "(未配置 AI,这是示例拆解)" : "每一行都能改,不满意就在下面和 AI 讨论,改到满意再确认。") + (ctx ? ` 已联网查到:${ctx}。` : "");
  } catch (e) { $("#g-msg").textContent = e.message; } finally { $("#g-decompose").disabled = false; }
};
$("#g-discuss").onclick = async () => {
  const fb = $("#g-feedback").value.trim(); if (!fb) return;
  $("#g-note").textContent = "AI 修改中…"; $("#g-discuss").disabled = true;
  try {
    // 把用户手改过的当前版本也带上,AI 在它之上改
    const hist = HISTORY.concat([{ role: "assistant", content: "当前版本:\n" + tasksAsText(PREVIEW) }]);
    const r = await api("/api/goals/discuss", "POST", { title: $("#g-title").value.trim(), due: $("#g-due").value || null, history: hist, feedback: fb });
    if (r.error || !r.tasks.length) { $("#g-note").textContent = r.error || "模型没有返回内容,保留了你当前的版本"; return; }
    HISTORY = hist.concat([{ role: "user", content: fb }, { role: "assistant", content: tasksAsText(r.tasks) }]);
    PREVIEW = r.tasks; renderPreview(); $("#g-note").textContent = "AI:" + r.note; $("#g-feedback").value = "";
  } catch (e) { $("#g-note").textContent = e.message; } finally { $("#g-discuss").disabled = false; }
};
$("#g-add-row").onclick = () => { PREVIEW.push({ title: "", est_hours: 1, due: $("#g-due").value || null, resource: null }); renderPreview(); };
$("#g-confirm").onclick = async () => {
  const tasks = PREVIEW.filter(t => t.title.trim());
  await api("/api/goals", "POST", { title: $("#g-title").value.trim(), due: $("#g-due").value || null, tasks });
  PREVIEW = []; HISTORY = []; $("#g-preview").hidden = true; $("#g-title").value = ""; $("#g-msg").textContent = ""; loadGoals();
};
async function loadGoals() {
  loadGrades().catch(() => {});
  const gs = await api("/api/goals"); const box = $("#goal-list"); box.innerHTML = "";
  if (!gs.length) box.append(el("div", { class: "card" }, el("p", { class: "muted" }, "还没有目标。上面立一个试试。")));
  for (const g of gs) {
    const done = g.tasks.filter(t => t.status === "done").length, n = g.tasks.length;
    const st = g.status === "achieved" ? el("span", { class: "tag canvas" }, "已达成") : g.status === "dropped" ? el("span", { class: "tag" }, "已放弃") : "";
    const card = el("div", { class: "card goal", style: `--gc:${goalColor(g.id)}` },
      el("div", { class: "goal-head" }, el("b", {}, g.title, st), el("span", { class: "row" }, el("span", { class: "muted" }, `${g.due ? "截止 " + g.due + " · " : ""}完成 ${done}/${n}`),
        el("button", { class: "small", onclick: (e) => { const c = e.target.closest(".goal"); c.classList.toggle("collapsed"); e.target.textContent = c.classList.contains("collapsed") ? "展开目标" : "折叠目标"; } }, "折叠目标"),
        el("button", { class: "small danger", onclick: async () => { if (confirm(`删除目标「${g.title}」和它的 ${n} 个任务?`)) { await api(`/api/goals/${g.id}`, "DELETE"); loadGoals(); } } }, "删除目标"))),
      el("div", { class: "bar" }, el("i", { style: `width:${n ? done / n * 100 : 0}%` })));
    const ul = el("ul", { class: "tasks" });
    for (const t of g.tasks) ul.append(taskLi(t, [t.status === "done" ? el("span", { class: "tag" }, "已完成") : editBtn(t, loadGoals), t.status === "done" ? "" : el("button", { class: "small danger", onclick: async () => { await api(`/api/tasks/${t.id}`, "DELETE"); loadGoals(); } }, "删")]));
    card.append(ul);
    const ti = el("input", { placeholder: "在这个目标下再加一件事" }), hi = el("input", { type: "number", step: "0.5", value: "1" }), di = el("input", { type: "date" });
    card.append(el("div", { class: "add-inline" }, ti, hi, di, el("button", { class: "small", onclick: async () => { if (!ti.value.trim()) return; await api("/api/tasks", "POST", { title: ti.value.trim(), est_hours: +hi.value || 1, due: di.value || null, goal_id: g.id }); loadGoals(); } }, "添加")));
    box.append(card);
  }
}

// ── 日历:周视图(默认)+ 月历 ──
async function loadCalendar() {
  if (CAL_MODE === "week") return loadWeekView();
  const q = CAL.y ? `?year=${CAL.y}&month=${CAL.m}` : "";
  const m = await api("/api/month" + q); CAL = { y: m.year, m: m.month };
  $("#cal-title").textContent = `${m.year} 年 ${m.month} 月`;
  ledgerCard($("#cal-ledger"), m.week);
  $("#week-wrap").hidden = true; $("#month-wrap").hidden = false;
  const grid = $("#cal-grid"); grid.innerHTML = "";
  for (const d of m.days) {
    const isMon = new Date(d.date + "T00:00:00").getDay() === 1;
    const cell = el("div", { class: "cal-cell" + (d.in_month ? "" : " out") + (d.is_today ? " today" : ""), onclick: () => showDay(d) },
      el("div", { class: "d" }, String(+d.date.slice(8)), isMon && d.week_no ? el("span", { class: "wk" }, `第${d.week_no}周`) : ""));
    for (const s of d.classes) cell.append(el("span", { class: "cls", style: `--tc:${colorOfClass(s)}`, title: `${s.name} ${s.location || ""}` }, `${s.slot_start}-${s.slot_end}节 ${s.name}`));
    d.tasks.slice(0, 4).forEach(t => cell.append(el("span", { class: "tk" + (t.due && t.due < m.today && t.status !== "done" ? " overdue" : ""), style: `--tc:${colorOf(t)}`, title: t.title }, t.title)));
    if (d.tasks.length > 4) cell.append(el("div", { class: "more" }, `+${d.tasks.length - 4} 件`));
    else if (d.tasks.length) cell.append(el("div", { class: "more", style: "display:none" }, `${d.tasks.length} 件`));
    grid.append(cell);
  }
  $("#cal-day").hidden = true;
}
async function loadWeekView() {
  const base = new Date(); base.setDate(base.getDate() + 7 * WEEK_OFF);
  const w = await api(`/api/week?date=${fmtDate(base)}`);
  $("#cal-title").textContent = `${w.monday} ~ ${w.sunday}${w.week_no ? " · 第 " + w.week_no + " 教学周" : ""}`;
  ledgerCard($("#cal-ledger"), w);
  $("#week-wrap").hidden = false; $("#month-wrap").hidden = true;
  const grid = $("#week-grid"); grid.innerHTML = ""; const tstr = todayStr(); const cap = 10;
  const DAYS = ["一", "二", "三", "四", "五", "六", "日"];
  for (const d of w.days) {
    const cls = d.slots.reduce((a, s) => a + ((s.slot_end || s.slot_start || 1) - (s.slot_start || 1) + 1) * 0.75, 0);
    const tk = d.tasks.reduce((a, t) => a + (+t.remaining_hours || 0), 0), used = cls + tk;
    const box = el("div", { class: "week-day" + (d.date === tstr ? " today" : ""), onclick: () => showDay({ date: d.date, classes: d.slots, tasks: d.tasks, is_today: d.date === tstr, week_no: w.week_no }) },
      el("h4", {}, `周${DAYS[d.weekday - 1]}`, el("span", { class: "muted" }, d.date.slice(5))));
    box.append(el("div", { class: "sec" }, d.slots.length ? `课 ${cls.toFixed(1)}h` : "没课"));
    for (const s of d.slots) box.append(el("span", { class: "cls", style: `--tc:${colorOfClass(s)}` }, `${s.slot_start}-${s.slot_end}节 ${s.name}`));
    box.append(el("div", { class: "sec" }, d.tasks.length ? `任务 ${tk.toFixed(1)}h` : "无任务"));
    for (const t of d.tasks) box.append(el("span", { class: "tk" + (t.due && t.due < tstr && t.status !== "done" ? " overdue" : ""), style: `--tc:${colorOf(t)}`, title: t.title }, `${t.title}(${t.remaining_hours}h)`));
    const mini = el("div", { class: "mini" }); const total = Math.max(cap, used);
    mini.append(el("i", { class: "c", style: `width:${cls / total * 100}%` }), el("i", { class: "t", style: `width:${tk / total * 100}%` }));
    box.append(mini, el("div", { class: "load" + (used > cap ? " over" : "") }, `${used.toFixed(1)} / ${cap}h${used > cap ? " 超" : ""}`));
    grid.append(box);
  }
  $("#cal-day").hidden = true;
}
$("#cal-mode").onclick = () => { CAL_MODE = CAL_MODE === "week" ? "month" : "week"; $("#cal-mode").textContent = CAL_MODE === "week" ? "切到月历" : "切到周视图"; $("#cal-today").textContent = CAL_MODE === "week" ? "本周" : "本月"; loadCalendar(); };
function showDay(d) {
  const box = $("#cal-day"); box.hidden = false; box.innerHTML = "";
  box.append(el("h2", {}, `${d.date} `, el("span", { class: "muted" }, d.week_no ? `第 ${d.week_no} 教学周` : "")));
  const st = el("div"); box.append(el("h3", {}, "负载条"), st);
  renderStrip(st, d.classes || [], d.tasks, 10);
  if (d.classes.length) { const ul = el("ul", { class: "tasks" }); for (const s of d.classes) ul.append(classLi(s)); box.append(el("h3", { style: "margin-top:12px" }, "课"), ul); }
  const ul = el("ul", { class: "tasks" });
  if (!d.tasks.length) ul.append(el("li", { class: "empty" }, "这天没有任务。"));
  const tstr = todayStr();
  for (const t of d.tasks) {
    const di = el("input", { type: "date", value: d.date, title: "腾挪到哪天" });
    const move = async (to) => { await api(`/api/tasks/${t.id}`, "PATCH", { scheduled_date: to }); toast(`「${t.title}」已腾挪到 ${to}${t.due && to > t.due ? "(已越过截止日 " + t.due + ")" : ""}`, t.due && to > t.due); loadCalendar(); };
    ul.append(taskLi(t, [
      d.is_today && t.status === "confirmed" ? el("button", { class: "small primary", onclick: async () => { await api(`/api/review/${t.id}/done`, "POST"); loadCalendar(); } }, "完成") : "",
      editBtn(t, loadCalendar),
      t.status === "confirmed" ? el("span", { class: "row", style: "margin:0;gap:4px" }, el("button", { class: "small", onclick: () => { if (di.value && di.value !== d.date) move(di.value); } }, "腾挪到"), di) : "",
      el("button", { class: "small danger", onclick: async () => { await api(`/api/tasks/${t.id}`, "DELETE"); loadCalendar(); } }, "删")]));
  }
  box.append(el("h3", {}, "任务"), ul);
  const ti = el("input", { placeholder: "给这天加一件事" }), hi = el("input", { type: "number", step: "0.5", value: "1" });
  box.append(el("div", { class: "row" }, ti, hi, el("button", { class: "small primary", onclick: async () => { if (!ti.value.trim()) return; await api("/api/tasks", "POST", { title: ti.value.trim(), est_hours: +hi.value || 1, due: d.date, scheduled_date: d.date }); loadCalendar(); } }, "添加")));
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
}
function shiftMonth(n) { let { y, m } = CAL; if (!y) { const d = new Date(); y = d.getFullYear(); m = d.getMonth() + 1; } m += n; if (m > 12) { m = 1; y++; } if (m < 1) { m = 12; y--; } CAL = { y, m }; loadCalendar(); }
$("#cal-prev").onclick = () => { if (CAL_MODE === "week") { WEEK_OFF--; loadWeekView(); } else shiftMonth(-1); };
$("#cal-next").onclick = () => { if (CAL_MODE === "week") { WEEK_OFF++; loadWeekView(); } else shiftMonth(1); };
$("#cal-today").onclick = () => { if (CAL_MODE === "week") { WEEK_OFF = 0; loadWeekView(); } else { CAL = { y: null, m: null }; loadCalendar(); } };

// ── 接入 ──
let SLOTS = [];
async function loadIntegrations() {
  const [s, pend] = await Promise.all([api("/api/integrations/status"), api("/api/tasks?status=pending")]);
  $("#pending-panel").hidden = !pend.length; renderPending($("#pending-list-int"), pend, loadIntegrations); updateBadge(pend.length);
  const set = (id, on, txt) => { const e = $(id); e.textContent = txt; e.className = "status" + (on ? " on" : ""); };
  set("#int-sched-status", s.schedule_slots > 0, s.schedule_slots > 0 ? `已导入 ${s.schedule_slots} 条` : "未导入");
  set("#int-canvas-status", s.canvas.configured, s.canvas.configured ? `已配置 · 已拉 ${s.canvas.tasks} 项` : "未配置");
  set("#int-mail-status", s.mail.configured, s.mail.configured ? `${s.mail.user} · 已抽 ${s.mail.tasks} 项` : "未配置");
  $("#int-mail-user").value = s.mail.user || "";
}
function renderSlots() {
  const DAYN = ["", "一", "二", "三", "四", "五", "六", "日"];
  const tb = $("#sch-rows"); tb.innerHTML = "";
  SLOTS.forEach((s, i) => tb.append(el("tr", {},
    el("td", {}, el("input", { value: s.name, oninput: e => s.name = e.target.value })),
    el("td", {}, el("input", { value: DAYN[s.day] || s.day, oninput: e => { const v = e.target.value.trim(); s.day = DAYN.indexOf(v) > 0 ? DAYN.indexOf(v) : (+v || s.day); } })),
    el("td", {}, el("input", { value: `${s.slot_start}-${s.slot_end}`, oninput: e => { const m = e.target.value.match(/(\d+)\D+(\d+)/); if (m) { s.slot_start = +m[1]; s.slot_end = +m[2]; } } })),
    el("td", {}, el("input", { value: (s.weeks || []).join(","), oninput: e => s.weeks = e.target.value.split(/[,，\s]+/).map(Number).filter(Boolean) })),
    el("td", {}, el("input", { value: s.location || "", oninput: e => s.location = e.target.value })),
    el("td", {}, el("button", { class: "small link", onclick: () => { SLOTS.splice(i, 1); renderSlots(); } }, "删")))));
  $("#sch-preview").hidden = false;
}
// 课表文件:PDF 先在浏览器里用 pdf.js 抽文本(服务器不必装依赖),失败再交给服务器
const PDFJS = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.4.168/";
async function pdfToText(buf) {
  const pdfjs = await import(PDFJS + "pdf.min.mjs");
  pdfjs.GlobalWorkerOptions.workerSrc = PDFJS + "pdf.worker.min.mjs";
  const doc = await pdfjs.getDocument({ data: buf }).promise; const lines = [];
  for (let p = 1; p <= doc.numPages; p++) {
    const tc = await (await doc.getPage(p)).getTextContent();
    let lastY = null, line = [];
    for (const it of tc.items) { const y = Math.round(it.transform[5]); if (lastY !== null && Math.abs(y - lastY) > 2) { lines.push(line.join(" ")); line = []; } line.push(it.str); lastY = y; }
    if (line.length) lines.push(line.join(" "));
  }
  return lines.join("\n");
}
// 图片:浏览器内 OCR(tesseract.js,中英文),再交给 AI 解析
async function imageToText(file, onProgress) {
  if (!window.Tesseract) await new Promise((ok, bad) => { const s = document.createElement("script"); s.src = "https://cdnjs.cloudflare.com/ajax/libs/tesseract.js/5.1.1/tesseract.min.js"; s.onload = ok; s.onerror = () => bad(new Error("OCR 库加载失败(需要外网)")); document.head.append(s); });
  const worker = await Tesseract.createWorker(["chi_sim", "eng"], 1, { logger: m => { if (m.status === "recognizing text" && onProgress) onProgress(Math.round(m.progress * 100)); } });
  try { const { data } = await worker.recognize(file); return data.text; } finally { await worker.terminate(); }
}
$("#sch-file").onchange = async (e) => {
  const f = e.target.files[0]; if (!f) return;
  const name = f.name.toLowerCase(); $("#sch-file-msg").textContent = "读取中…";
  try {
    let text;
    if (name.endsWith(".pdf")) {
      try { text = await pdfToText(await f.arrayBuffer()); }
      catch (err) { const fd = new FormData(); fd.append("file", f); const r = await fetch("/api/schedule/upload", { method: "POST", body: fd }); const j = await r.json(); if (!r.ok) throw new Error(j.detail || "上传失败"); text = j.text; }
    } else if (/\.(png|jpe?g|webp|gif|bmp)$/.test(name) || f.type.startsWith("image/")) {
      $("#sch-file-msg").textContent = "识别图片文字中(首次要下载语言包,约 20 秒)…";
      text = await imageToText(f, p => $("#sch-file-msg").textContent = `识别图片文字 ${p}%…`);
    } else text = await f.text();
    if (!text.trim()) throw new Error("没读出文字:换一张更清晰的截图,或粘贴文字");
    $("#sch-text").value = text; $("#sch-file-msg").textContent = `已读出 ${text.length} 字,交给 AI 解析…`;
    $("#sch-parse").click();
  } catch (err) { $("#sch-file-msg").textContent = err.message; }
  e.target.value = "";
};
$("#sch-parse").onclick = async () => {
  $("#sch-msg").textContent = "解析中…";
  try { const r = await api("/api/schedule/parse", "POST", { text: $("#sch-text").value }); SLOTS = r.slots; renderSlots(); $("#sch-msg").textContent = `识别出 ${SLOTS.length} 门课${r.llm === "stub" ? "(规则解析)" : ""},核对后保存。`; }
  catch (e) { $("#sch-msg").textContent = e.message; }
};
$("#sch-save").onclick = async () => { const r = await api("/api/schedule", "PUT", { slots: SLOTS }); $("#sch-msg").textContent = `已保存 ${r.count} 门课;本周可投入时长更新为 ${r.weekly_hours} 小时。`; loadIntegrations(); };
$("#int-canvas-save").onclick = async () => { await api("/api/settings", "PUT", { canvas_token: $("#int-canvas-token").value }); $("#canvas-msg").textContent = "已保存"; loadIntegrations(); };
$("#int-canvas-import").onclick = async () => {
  $("#canvas-msg").textContent = "拉取中…";
  try { const r = await api("/api/canvas/import", "POST"); $("#canvas-msg").textContent = `拉到 ${r.fetched} 项,新进待确认 ${r.added} 项。`; loadIntegrations(); }
  catch (e) { $("#canvas-msg").textContent = e.message; }
};
$("#int-mail-save").onclick = async () => { await api("/api/settings", "PUT", { mail_user: $("#int-mail-user").value, mail_pass: $("#int-mail-pass").value }); $("#mail-msg").textContent = "已保存"; $("#int-mail-pass").value = ""; loadIntegrations(); };
$("#int-mail-import").onclick = async () => {
  $("#mail-msg").textContent = "读取中,可能要十几秒…";
  try { const r = await api("/api/mail/import", "POST"); $("#mail-msg").textContent = `读了 ${r.fetched} 封,抽出 ${r.found} 件事,新进待确认 ${r.added} 件。`; loadIntegrations(); }
  catch (e) { $("#mail-msg").textContent = e.message; }
};

// ── 小组 ──
async function loadGroups() {
  const gs = await api("/api/groups"); const box = $("#grp-list"); box.innerHTML = "";
  for (const g of gs) box.append(el("div", { class: "grp", onclick: () => loadGroup(g.id) }, g.name));
  if (!gs.length) box.append(el("span", { class: "muted" }, "还没加入小组。建一个,或输入同伴给你的邀请码。"));
}
$("#grp-create").onclick = async () => { if (!$("#grp-name").value.trim()) return; const r = await api("/api/groups", "POST", { name: $("#grp-name").value.trim() }); $("#grp-msg").textContent = `已建组,邀请码 ${r.invite_code}(24 小时有效)`; loadGroups(); loadGroup(r.id); };
$("#grp-join").onclick = async () => { try { const r = await api("/api/groups/join", "POST", { code: $("#grp-code").value }); $("#grp-msg").textContent = `已加入「${r.name}」`; loadGroups(); loadGroup(r.id); } catch (e) { $("#grp-msg").textContent = e.message; } };
async function loadGroup(id) {
  const g = await api(`/api/groups/${id}`); const box = $("#grp-detail"); box.hidden = false; box.innerHTML = "";
  const head = el("div", { class: "card" }, el("h2", {}, g.name, g.invite_code ? el("span", { class: "muted" }, ` · 邀请码 ${g.invite_code}`) : ""));
  const tbl = el("table", { class: "grid" }, el("thead", {}, el("tr", {}, el("th", {}, "排名"), el("th", {}, "成员"), el("th", {}, "认领"), el("th", {}, "完成"), el("th", {}, "完成率(只算小组任务)"), el("th", {}, "完成时长"), el("th", {}, "连续天数"))));
  const tb = el("tbody"); for (const m of g.members) tb.append(el("tr", {}, el("td", { class: "num" }, m.rank === 1 ? "🥇" : m.rank === 2 ? "🥈" : m.rank === 3 ? "🥉" : String(m.rank)), el("td", {}, m.username + (m.id === ME.id ? "(我)" : "")), el("td", {}, String(m.claimed)), el("td", {}, String(m.done)), el("td", {}, `${Math.round(m.completion_rate * 100)}%`), el("td", {}, `${m.hours_done}h`), el("td", {}, String(m.streak)))); tbl.append(tb); head.append(tbl);
  const ti = el("input", { class: "grow", placeholder: "共同目标,例:共读《线性代数应该这样学》前四章" }), di = el("input", { type: "date" });
  head.append(el("h3", { style: "margin-top:14px" }, "共同目标"), el("div", { class: "row" }, ti, di, el("button", { class: "primary", onclick: async () => {
    if (!ti.value.trim()) return; const r = await api("/api/goals/decompose", "POST", { title: ti.value.trim(), due: di.value || null });
    await api("/api/goals", "POST", { title: ti.value.trim(), due: di.value || null, tasks: r.tasks, group_id: id }); loadGroup(id);
  } }, "提出共同目标")));
  // 小组任务(不挂目标)
  const gt = el("div", { class: "card" }, el("h3", {}, "小组任务 ", el("span", { class: "muted" }, "不挂在某个目标下的事,谁都能认领")));
  const gul = el("ul", { class: "tasks" });
  if (!g.group_tasks.length) gul.append(el("li", { class: "empty" }, "还没有小组任务。"));
  for (const t of g.group_tasks) gul.append(taskLi(t, [
    t.assignee ? el("span", { class: "tag" }, t.status === "done" ? `${t.assignee} 已完成` : `${t.assignee} 认领`) : el("button", { class: "small primary", onclick: async () => { await api(`/api/tasks/${t.id}/claim`, "POST"); loadGroup(id); } }, "我来认领"),
    t.assignee && t.status !== "done" && t.user_id === ME.id ? el("button", { class: "small", onclick: async () => { await api(`/api/review/${t.id}/done`, "POST"); loadGroup(id); } }, "完成") : "",
    t.status !== "done" ? editBtn(t, () => loadGroup(id)) : ""]));
  gt.append(gul);
  const gti = el("input", { placeholder: "加一件小组任务,例:订下周三的自习室" }), ghi = el("input", { type: "number", step: "0.5", value: "1" }), gdi = el("input", { type: "date" });
  gt.append(el("div", { class: "add-inline" }, gti, ghi, gdi, el("button", { class: "small", onclick: async () => { if (!gti.value.trim()) return; await api("/api/tasks", "POST", { title: gti.value.trim(), est_hours: +ghi.value || 1, due: gdi.value || null, group_id: id }); loadGroup(id); } }, "添加")));
  box.append(head, gt);
  for (const gl of g.goals) {
    const st = gl.status === "achieved" ? el("span", { class: "tag canvas" }, "已达成") : gl.status === "dropped" ? el("span", { class: "tag" }, "已放弃") : (gl.days_left != null ? el("span", { class: cdClass(gl.days_left) }, daysLabel(gl.days_left)) : "");
    const c = el("div", { class: "card goal", style: `--gc:${goalColor(gl.id)}` }, el("div", { class: "goal-head" }, el("b", {}, gl.title, " ", st), el("span", { class: "row" }, el("span", { class: "muted" }, `${gl.due ? "⌛ " + gl.due + " · " : ""}进度 ${Math.round(gl.progress * 100)}%`), el("button", { class: "small", onclick: (e) => { const cc = e.target.closest(".goal"); cc.classList.toggle("collapsed"); e.target.textContent = cc.classList.contains("collapsed") ? "展开目标" : "折叠目标"; } }, "折叠目标"))), el("div", { class: "bar" }, el("i", { style: `width:${gl.progress * 100}%` })));
    const ul = el("ul", { class: "tasks" });
    if (!gl.tasks.length) ul.append(el("li", { class: "empty" }, "这个共同目标还没有任务,在下面加。"));
    for (const t of gl.tasks) ul.append(taskLi(t, [
      t.assignee ? el("span", { class: "tag" }, t.status === "done" ? `${t.assignee} 已完成` : `${t.assignee} 认领`) : el("button", { class: "small primary", onclick: async () => { await api(`/api/tasks/${t.id}/claim`, "POST"); loadGroup(id); } }, "我来认领"),
      t.assignee && t.status !== "done" && t.user_id === ME.id ? el("button", { class: "small", onclick: async () => { await api(`/api/review/${t.id}/done`, "POST"); loadGroup(id); } }, "完成") : "",
      t.status !== "done" ? editBtn(t, () => loadGroup(id)) : ""]));
    c.append(ul);
    if (gl.status === "active" && gl.tasks.some(t => !t.assignee)) {
      c.append(el("div", { class: "row" }, el("button", { class: "small primary", onclick: async () => { const r = await api(`/api/goals/${gl.id}/claim_all`, "POST"); toast(`已认领 ${r.claimed} 件`); loadGroup(id); } }, `一键认领剩余 ${gl.tasks.filter(t => !t.assignee).length} 件`)));
    }
    if (gl.status === "active") {
      const ti = el("input", { placeholder: "给共同目标加一件事" }), hi = el("input", { type: "number", step: "0.5", value: "1" }), di = el("input", { type: "date", value: gl.due || "" });
      c.append(el("div", { class: "add-inline" }, ti, hi, di, el("button", { class: "small", onclick: async () => { if (!ti.value.trim()) return; await api("/api/tasks", "POST", { title: ti.value.trim(), est_hours: +hi.value || 1, due: di.value || null, goal_id: gl.id }); loadGroup(id); } }, "添加")));
    }
    box.append(c);
  }
}

// ── 设置 ──
async function loadSettings() {
  ME = await api("/api/me");
  $("#s-hours").value = ME.weekly_hours; $("#s-start").value = ME.semester_start || ""; $("#s-llm").textContent = ME.llm === "deepseek" ? "DeepSeek 已连接" : "未配置(示例模式)";
  const sel = $("#r-hour"); if (sel.options.length <= 1) for (let h = 6; h <= 23; h++) sel.append(el("option", { value: h }, `${String(h).padStart(2, "0")}:00`));
  sel.value = ME.remind_hour == null ? "" : String(ME.remind_hour);
  $("#r-last").textContent = ME.last_remind ? `上次发送:${ME.last_remind.replace("T", " ")}` : "还没发过。";
  $("#acct-name").textContent = ME.username;
}
$("#s-adopt").onclick = async () => { const r = await api("/api/settings/adopt_suggested", "POST"); $("#s-hours").value = r.weekly_hours; $("#s-msg").textContent = `已按课表重算:${r.weekly_hours} 小时`; };
$("#r-save").onclick = async () => { const v = $("#r-hour").value; await api("/api/remind", "PUT", { hour: v === "" ? null : +v }); $("#r-msg").textContent = v === "" ? "已关闭提醒" : `每天 ${String(v).padStart(2, "0")}:00 发送`; };
$("#r-send").onclick = async () => { $("#r-msg").textContent = "发送中…"; try { const r = await api("/api/remind/send_now", "POST"); $("#r-msg").textContent = `已发到 ${r.to}(${r.sent_at.replace("T", " ")})`; $("#r-last").textContent = `上次发送:${r.sent_at.replace("T", " ")}`; } catch (e) { $("#r-msg").textContent = e.message; } };
$("#pw-save").onclick = async () => { try { await api("/api/account/password", "POST", { old_password: $("#pw-old").value, new_password: $("#pw-new").value }); $("#pw-msg").textContent = "密码已修改,其他设备已下线"; $("#pw-old").value = $("#pw-new").value = ""; } catch (e) { $("#pw-msg").textContent = e.message; } };
$("#acct-logout").onclick = async () => { await api("/api/logout", "POST"); location.reload(); };
$("#s-save").onclick = async () => {
  try { await api("/api/settings", "PUT", { weekly_hours: +$("#s-hours").value, semester_start: $("#s-start").value || null }); $("#s-msg").textContent = "已保存"; }
  catch (e) { $("#s-msg").textContent = e.message; }
};

boot();
