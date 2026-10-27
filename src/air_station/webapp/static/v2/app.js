/* 前端 v2：对接 C 模块后端 API（经同源 /capi 反代） */
(function () {
  "use strict";
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const cache = {};
  const state = { scadaTimer: null, lastPlan: null, sessionId: null };

  const PAGE_META = {
    system: ["系统总览", "SCADA / 健康 / 剩余寿命 / 喘振 / 知识库状态"],
    scada: ["实时监控", "模拟 SCADA 站级快照与单机历史"],
    models: ["预测与诊断", "调用 C 后端 forecast / health / rul / surge"],
    dispatch: ["智能调度", "四模式调度求解与历史方案"],
    control: ["控制执行", "模拟 PLC / SCADA / EAM 指令闭环"],
    agent: ["Agent 交互", "自然语言意图识别与工具编排"],
    knowledge: ["知识库", "项目知识问答与引用来源"],
    metrics: ["指标与验收", "预测与调度对比指标"],
  };
  const MODE_CN = { b3: "b3 能效优先", balanced: "balanced 低负荷优先", load_balanced: "load_balanced 加载率均衡", joint_nompc: "joint_nompc 多目标" };

  async function capi(path, opts) {
    const res = await fetch("/capi" + path, opts);
    const text = await res.text();
    let data;
    try { data = JSON.parse(text); } catch (e) { data = { status: "error", error: { message: text.slice(0, 200) } }; }
    if (!res.ok && data.status !== "error") data = { status: "error", error: { message: "HTTP " + res.status } };
    return data;
  }
  function post(path, body) {
    return capi(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  }
  function errMsg(d) { return (d && d.error && d.error.message) ? d.error.message : "请求失败"; }
  function fmt(v, d) {
    if (v === null || v === undefined || (typeof v === "number" && !isFinite(v))) return "—";
    if (typeof v === "number") return Math.abs(v) >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 0 }) : Number(v.toFixed(d === undefined ? 2 : d)).toString();
    return String(v);
  }
  function pct(v, d) { return (v === null || v === undefined) ? "—" : fmt(v, d === undefined ? 1 : d) + "%"; }
  function esc(s) { return String(s === null || s === undefined ? "" : s).replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }
  function table(cols, rows) {
    let h = "<table><thead><tr>";
    cols.forEach(c => h += `<th class="${c.num ? "num" : ""}">${c.label}</th>`);
    h += "</tr></thead><tbody>";
    (rows || []).forEach(r => {
      h += "<tr>";
      cols.forEach(c => { const v = typeof c.value === "function" ? c.value(r) : r[c.key]; h += `<td class="${c.num ? "num" : ""}">${v == null || v === "" ? "—" : v}</td>`; });
      h += "</tr>";
    });
    return h + "</tbody></table>";
  }
  function infoRows(pairs) { return pairs.map(p => `<div class="kb-statusbar" style="display:flex"><b>${p[0]}</b> ${p[1] == null || p[1] === "" ? "—" : p[1]}</div>`).join(""); }
  function kpi(label, value, unit, sub) { return `<div class="kpi"><div class="k-label">${label}</div><div class="k-value">${value}<span class="k-unit">${unit || ""}</span></div><div class="k-sub">${sub || ""}</div></div>`; }
  function riskPill(level) {
    const m = { 正常: "ok", 低: "ok", low: "ok", 关注: "info", attention: "info", 预警: "warn", medium: "warn", 严重: "err", high: "err", unknown: "", 未知: "" };
    const cls = m[String(level)] || "";
    const cn = { low: "低", medium: "中", high: "高", unknown: "未知" }[String(level)] || level;
    return `<span class="pill ${cls}">${esc(cn)}</span>`;
  }
  function citationList(citations) {
    if (!citations || !citations.length) return "";
    const seen = new Set();
    const items = citations.filter(c => { const k = c.knowledge_id || c.title; if (seen.has(k)) return false; seen.add(k); return true; })
      .map((c, i) => `<div class="cite-item"><div class="cite-title">参考依据 ${i + 1} · ${esc(c.title || "")}</div>
        <div class="cite-meta">分类：${esc(c.category || "—")} ｜ 章节：${esc(c.section || "—")}</div>
        <div class="cite-meta">来源：<span class="mono">${esc(c.source_path || "—")}</span></div>
        <div class="cite-meta">相关度：${c.score == null ? "—" : fmt(c.score * 100, 1) + "%"}</div></div>`).join("");
    return `<details class="cites"><summary>参考依据（${citations.length}）</summary>${items}</details>`;
  }
  function kbStatusPill(st) {
    const s = st.kb_status || (st.stale ? "stale" : "fresh");
    if (s === "stale") return '<span class="pill warn">索引已过期</span>';
    if (st.index_ok === false) return '<span class="pill err">索引不可用</span>';
    return '<span class="pill ok">正常</span>';
  }

  /* ------------------------------ 系统总览 ------------------------------ */
  async function renderSystem() {
    const ov = await capi("/api/system/overview");
    if (ov.status !== "success") { $("#sys-scada").innerHTML = `<div class="warn-banner">${esc(errMsg(ov))}</div>`; return; }
    const scada = ov.scada || {}, health = ov.health || {}, rul = ov.rul || {}, surge = ov.surge || {};
    const dev0 = (scada.devices || [])[0] || {};
    const totalFlow = (scada.devices || []).reduce((s, d) => s + (d.flow_m3_min || 0), 0);
    const totalPower = (scada.devices || []).reduce((s, d) => s + (d.power_kw || 0), 0);
    $("#sys-kpis").innerHTML = [
      kpi("站级总供气", fmt(totalFlow, 1), "m³/min", "模拟 SCADA 快照"),
      kpi("站级总功率", fmt(totalPower, 0), "kW", "两台机组"),
      kpi("母管压力", fmt(dev0.discharge_pressure_mpa, 3), "MPa", "单机排气压力"),
      kpi("4# 健康评分", fmt(((health.devices || [])[0] || {}).health_score), "分", "无监督"),
      kpi("喘振整体", riskPill(surge.station_risk_level || "unknown"), "", "代理分级，非真实喘振线"),
    ].join("");
    $("#sys-scada").innerHTML = `<div class="snap">${(scada.devices || []).map(d =>
      `<div class="snap-item"><div class="s-l">${esc(d.device_id)} ${d.running ? "运行" : "停机"}</div>
       <div class="s-v">${fmt(d.flow_m3_min, 1)} m³/min</div>
       <div class="hint">${fmt(d.power_kw, 0)} kW · IGV ${fmt(d.igv_percent, 0)}% · ${fmt(d.discharge_pressure_mpa, 3)} MPa</div></div>`).join("")}</div>
      <div class="hint">快照时间 ${esc(scada.snapshot_at || "—")} ｜ 来源 ${esc(scada.source || "—")}</div>`;
    $("#sys-health").innerHTML =
      table([{ label: "设备", key: "d" }, { label: "健康", num: true, key: "s" }, { label: "风险", key: "r" }, { label: "RUL(h)", num: true, key: "rul" }],
        (health.devices || []).map(d => {
          const rr = (rul.devices || []).find(x => x.device_id === d.device_id) || {};
          return { d: d.device_id, s: d.health_score, r: riskPill(d.risk_level), rul: fmt(rr.estimated_rul_hours, 1) };
        })) +
      `<div class="hint">RUL validation_mode=${esc(rul.validation_mode || "simulation")}（模拟）</div>`;
    const info = await capi("/api/system/info");
    $("#sys-info").innerHTML = infoRows([
      ["模块", esc(info.module || "—")],
      ["能力", (info.capabilities || []).join(", ")],
      ["调度模式", (info.dispatch_modes || []).join(", ")],
      ["MCP 工具", (info.mcp_tools || []).join(", ")],
    ]);
    renderSysKb();
  }
  async function renderSysKb() {
    const st = await capi("/api/knowledge/status");
    $("#sys-kb").innerHTML = st.status === "success" ? `
      <div class="ans-head">知识库 ${kbStatusPill(st)}</div>
      <div class="kb-statusbar"><span><b>文档</b>${st.n_docs}</span><span><b>知识块</b>${st.n_chunks}</span><span><b>版本</b>${esc(st.kb_version)}</span><span><b>构建时间</b>${esc(st.built_at || "—")}</span></div>
      ${st.kb_status === "stale" ? `<div class="hint">知识来源已更新，索引等待重建：${esc((st.stale_sources || []).join(", "))}</div>` : ""}`
      : `<div class="warn-banner">${esc(errMsg(st))}</div>`;
  }

  /* ------------------------------ 实时监控 ------------------------------ */
  async function renderScada() {
    if (!cache.scadaBound) {
      cache.scadaBound = true;
      $("#sc-load").addEventListener("click", loadScadaHistory);
      $("#sc-live").addEventListener("change", e => e.target.checked ? startScada() : stopScada());
      startScada();
    }
    await refreshScada();
  }
  function startScada() { stopScada(); state.scadaTimer = setInterval(refreshScada, 3000); }
  function stopScada() { if (state.scadaTimer) clearInterval(state.scadaTimer); state.scadaTimer = null; }
  async function refreshScada() {
    if (!$("#scada").classList.contains("active")) return;
    const d = await capi("/api/scada/realtime");
    if (d.status !== "success") { $("#sc-cards").innerHTML = `<div class="warn-banner">${esc(errMsg(d))}</div>`; return; }
    $("#sc-time").textContent = "快照 " + (d.snapshot_at || "");
    $("#sc-cards").innerHTML = (d.devices || []).map(x => `
      <div class="unit-card ${x.running ? "running" : ""}">
        <div class="unit-head"><span class="unit-name">${esc(x.device_id)} 空压机</span>${x.running ? '<span class="pill ok">运行</span>' : '<span class="pill">停机</span>'}</div>
        <div class="unit-metrics">
          <span class="um-l">流量</span><span class="um-v">${fmt(x.flow_m3_min, 1)} m³/min</span>
          <span class="um-l">功率</span><span class="um-v">${fmt(x.power_kw, 0)} kW</span>
          <span class="um-l">IGV</span><span class="um-v">${fmt(x.igv_percent, 0)} %</span>
          <span class="um-l">排气压力</span><span class="um-v">${fmt(x.discharge_pressure_mpa, 3)} MPa</span>
          <span class="um-l">连接</span><span class="um-v">${x.connected ? "正常" : "断开"}</span>
        </div>
      </div>`).join("");
  }
  async function loadScadaHistory() {
    const dev = $("#sc-dev").value, min = $("#sc-min").value;
    const d = await capi(`/api/scada/history?device_id=${encodeURIComponent(dev)}&minutes=${min}`);
    if (d.status !== "success") { $("#sc-chart").innerHTML = `<div class="warn-banner">${esc(errMsg(d))}</div>`; return; }
    const pts = d.points || d.series || [];
    if (!pts.length) { $("#sc-chart").innerHTML = '<div class="muted">无历史数据</div>'; return; }
    const labels = pts.map(p => String(p.t || p.time || "").slice(11, 16));
    Charts.line($("#sc-chart"), { labels, series: [
      { name: "流量 m³/min", color: "#2F6BFF", data: pts.map(p => p.flow_m3_min) },
      { name: "功率 kW", color: "#00B3A4", data: pts.map(p => p.power_kw), opacity: 0.85 },
    ] });
  }

  /* ------------------------------ 预测与诊断 ------------------------------ */
  let mdBound = false;
  async function renderModels() {
    if (!mdBound) {
      mdBound = true;
      $$("#models [data-m]").forEach(b => b.addEventListener("click", () => callModel(b.dataset.m)));
    }
    if (!$("#md-result").dataset.done) callModel("forecast");
  }
  async function callModel(kind) {
    $("#md-result").innerHTML = '<div class="muted">调用中…</div>';
    const d = await post("/api/" + kind, kind === "forecast" ? { horizons: [15, 30, 60] } : {});
    if (d.status !== "success") { $("#md-result").innerHTML = `<div class="warn-banner">${esc(errMsg(d))}</div>`; return; }
    let h = "";
    if (kind === "forecast") {
      h = table([{ label: "时域(min)", num: true, key: "h" }, { label: "目标时刻", key: "t" }, { label: "预测 m³/min", num: true, key: "p" }, { label: "下界", num: true, key: "lo" }, { label: "上界", num: true, key: "up" }],
        (d.predictions || []).map(p => ({ h: p.horizon_minutes, t: esc(String(p.target_time).replace("T", " ")), p: fmt(p.predicted_flow, 2), lo: fmt(p.lower_bound, 1), up: fmt(p.upper_bound, 1) })));
      h += `<div class="hint">模型 ${esc(d.model_type)} ｜ 基准时刻 ${esc(d.prediction_base_time)} ｜ 耗时 ${fmt(d.elapsed_seconds, 3)}s</div>`;
    } else if (kind === "health") {
      h = table([{ label: "设备", key: "d" }, { label: "评分", num: true, key: "s" }, { label: "风险", key: "r" }, { label: "主因", key: "f" }, { label: "建议", key: "g" }],
        (d.devices || []).map(x => ({ d: x.device_id, s: x.health_score, r: riskPill(x.risk_level), f: esc(x.top_anomaly_factor), g: esc(x.suggestion) })));
      h += `<div class="hint">validation_mode=${esc(d.validation_mode)}（无监督）</div>`;
    } else if (kind === "rul") {
      h = table([{ label: "设备", key: "d" }, { label: "估计(h)", num: true, key: "v" }, { label: "区间(h)", key: "r" }],
        (d.devices || []).map(x => ({ d: x.device_id, v: fmt(x.estimated_rul_hours, 1), r: `${fmt(x.lower_bound_hours, 1)} ~ ${fmt(x.upper_bound_hours, 1)}` })));
      h += `<div class="hint">validation_mode=${esc(d.validation_mode)}（模拟，不代表真实寿命）</div>`;
    } else if (kind === "surge") {
      h = table([{ label: "设备", key: "d" }, { label: "风险", key: "r" }, { label: "裕度", num: true, key: "m" }, { label: "说明", key: "s" }],
        (d.devices || []).map(x => ({ d: x.device_id, r: riskPill(x.risk_level), m: fmt(x.risk_margin, 3), s: esc(x.limit_suggestion || "") })));
      h += `<div class="hint">整体 ${riskPill(d.station_risk_level || "unknown")} ｜ ${d.safety_blocked ? '<span class="pill err">安全阻断</span>' : '<span class="pill ok">未阻断</span>'} ｜ 代理边界，非真实喘振线</div>`;
    }
    $("#md-result").innerHTML = h; $("#md-result").dataset.done = "1";
  }

  /* ------------------------------ 智能调度 ------------------------------ */
  let dpBound = false;
  async function renderDispatch() {
    if (!dpBound) { dpBound = true; $("#dp-run").addEventListener("click", runDispatch); }
    loadPlans();
    if (!$("#dp-result").dataset.done) runDispatch();
  }
  async function runDispatch() {
    const body = { mode: $("#dp-mode").value, persist: $("#dp-persist").checked };
    const dem = $("#dp-demand").value;
    if (dem !== "") body.demand_flow_m3_min = Number(dem);
    const btn = $("#dp-run"); btn.disabled = true; btn.textContent = "求解中…";
    $("#dp-result").innerHTML = '<div class="muted">求解中…</div>';
    const d = await post("/api/dispatch", body);
    btn.disabled = false; btn.textContent = "求解";
    if (d.status !== "success") { $("#dp-result").innerHTML = `<div class="warn-banner">${esc(errMsg(d))}</div>`; return; }
    state.lastPlan = d;
    const cm = d.c_meta || {};
    const cls = d.is_fallback ? "warn" : "ok";
    const pill = d.is_fallback ? '<span class="pill warn">安全回退</span>' : '<span class="pill ok">求解成功</span>';
    $("#dp-result").innerHTML = `
      <div class="ans-head">${pill}<span class="pill info">${esc(MODE_CN[d.mode] || d.mode)}</span>
        ${d.safety_blocked ? '<span class="pill err">安全阻断</span>' : ""}</div>
      <table><tbody>
        <tr><th>机组组合</th><td>${(d.target_combination || []).join(" + ") || "—"}</td><th>供气</th><td>${fmt(d.total_flow_m3_min, 1)} m³/min</td></tr>
        <tr><th>预计功率</th><td>${fmt(d.estimated_power_kw, 0)} kW</td><th>需求</th><td>${fmt(d.required_flow_m3_min, 1)} m³/min</td></tr>
        <tr><th>喘振状态</th><td>${riskPill(d.surge_status === "not_assessed_b4" ? "unknown" : d.surge_status)}</td><th>时延</th><td>${fmt(d.latency_ms, 0)} ms</td></tr>
        <tr><th>执行权限</th><td class="mono">${esc(d.execution_permission)}</td><th>可用性</th><td>candidate_usable=${esc(cm.candidate_usable)} · needs_human_review=${esc(cm.needs_human_review)}</td></tr>
      </tbody></table>
      <div class="card-title" style="margin-top:12px">机组动作</div>
      ${table([{ label: "设备", key: "d" }, { label: "动作", key: "a" }, { label: "目标流量", num: true, key: "f" }, { label: "负荷率", num: true, key: "lr" }, { label: "功率(kW)", num: true, key: "p" }],
        (d.device_actions || []).map(a => ({ d: a.device_id, a: a.action, f: fmt(a.target_flow_m3_min, 1), lr: fmt(a.target_load_ratio, 3), p: fmt(a.estimated_power_kw, 0) })))}
      ${cm.execution_allowed === false ? '<div class="hint">C 侧确认：execution_allowed=false（无真实执行许可）。</div>' : ""}`;
    $("#dp-result").dataset.done = "1";
    loadPlans();
  }
  async function loadPlans() {
    const d = await capi("/api/dispatch/plans");
    const plans = d.plans || [];
    $("#dp-plans").innerHTML = plans.length ? table([{ label: "plan_id", key: "p" }, { label: "模式", key: "m" }, { label: "组合", key: "c" }, { label: "功率", num: true, key: "w" }],
      plans.slice(0, 20).map(x => ({ p: esc(String(x.plan_id || x.id || "").slice(0, 12)), m: esc(x.mode || ""), c: esc((x.combination || []).join?.("+") || x.target_combination?.join?.("+") || ""), w: fmt(x.estimated_power_kw, 0) })))
      : '<div class="muted">暂无历史方案（求解时勾选“落库”可保存）。</div>';
  }

  /* ------------------------------ 控制执行 ------------------------------ */
  let ctBound = false;
  async function renderControl() {
    if (!ctBound) {
      ctBound = true;
      $("#ct-run").addEventListener("click", runCommand);
      $("#eam-load").addEventListener("click", loadEam);
      $("#wo-create").addEventListener("click", createWo);
      $("#wo-reload").addEventListener("click", loadWo);
    }
    loadEam(); loadWo();
  }
  async function runCommand() {
    const action = $("#ct-action").value;
    const body = { action, persist: true };
    if (action === "plc_command") { body.device_id = $("#ct-dev").value; body.action_type = "set_load"; body.target_flow_m3_min = Number($("#ct-flow").value); }
    if (action === "execute_plan") { if (!state.lastPlan) { $("#ct-result").innerHTML = '<div class="warn-banner">请先在“智能调度”求解一个方案</div>'; return; } body.plan = state.lastPlan; }
    if (action === "command_status") { const id = $("#ct-cid").value.trim(); if (id) { const r = await capi("/api/control/commands/" + encodeURIComponent(id)); $("#ct-result").innerHTML = `<div class="ans-head"><span class="pill info">指令状态</span></div><pre class="ans-body mono">${esc(JSON.stringify(r, null, 2))}</pre>`; return; } }
    const d = await post("/api/control/command", body);
    $("#ct-result").innerHTML = d.status === "success"
      ? `<div class="ans-head"><span class="pill ${d.accepted === false ? "err" : "ok"}">${esc(action)}</span>${d.rejected_reason ? `<span class="pill err">${esc(d.rejected_reason)}</span>` : ""}</div><pre class="ans-body mono">${esc(JSON.stringify(d, null, 2))}</pre>`
      : `<div class="warn-banner">${esc(errMsg(d))}</div>`;
  }
  async function loadEam() {
    const d = await capi("/api/eam/maintenance?device_id=" + encodeURIComponent($("#eam-dev").value));
    const dev = (d.devices || [])[0];
    $("#eam-result").innerHTML = dev ? table([{ label: "保养项目", key: "i" }, { label: "周期(h)", num: true, key: "c" }],
      (dev.plans || []).map(p => ({ i: esc(p["保养项目"]), c: p["保养周期_h"] }))) + `<div class="hint">型号 ${esc(dev.model || "—")} ｜ 模拟 EAM</div>`
      : `<div class="warn-banner">${esc(errMsg(d))}</div>`;
  }
  async function loadWo() {
    const d = await capi("/api/workorders");
    const wos = d.work_orders || [];
    $("#wo-result").innerHTML = wos.length ? table([{ label: "ID", key: "i" }, { label: "设备", key: "d" }, { label: "原因", key: "r" }, { label: "状态", key: "s" }],
      wos.map(w => ({ i: esc(String(w.id || w.work_order_id || "").slice(0, 10)), d: esc(w.device_id), r: esc(w.reason || ""), s: esc(w.status || "") })))
      : '<div class="muted">暂无工单。</div>';
  }
  async function createWo() {
    const d = await post("/api/workorders", { device_id: $("#wo-dev").value, reason: "前端演示创建", priority: "normal" });
    if (d.status !== "success") { $("#wo-result").innerHTML = `<div class="warn-banner">${esc(errMsg(d))}</div>`; return; }
    loadWo();
  }

  /* ------------------------------ Agent 交互 ------------------------------ */
  let agBound = false;
  async function renderAgent() {
    if (!agBound) {
      agBound = true;
      const examples = ["负荷预测使用什么模型？", "健康风险为什么会影响调度？", "5# 当前健康状况如何？", "请优化调度并解释为什么这样调整", "为什么高喘振风险方案不能执行？"];
      $("#ag-quick").innerHTML = examples.map(e => `<button data-q="${esc(e)}">${esc(e)}</button>`).join("");
      $("#ag-quick").addEventListener("click", e => { const b = e.target.closest("button"); if (b) sendAgent(b.dataset.q); });
      $("#ag-send").addEventListener("click", () => sendAgent($("#ag-input").value));
      $("#ag-input").addEventListener("keydown", e => { if (e.key === "Enter") sendAgent($("#ag-input").value); });
      pushChat("bot", "输入问题或选择示例；后端会返回意图、工具链与解释链路。");
    }
  }
  function pushChat(who, text) { const d = document.createElement("div"); d.className = "msg " + who; d.textContent = text; $("#ag-chat").appendChild(d); $("#ag-chat").scrollTop = $("#ag-chat").scrollHeight; }
  async function sendAgent(msg) {
    if (!msg || !msg.trim()) return;
    pushChat("user", msg); $("#ag-input").value = "";
    const d = await post("/api/agent/chat", { message: msg, execute: $("#ag-execute").checked, session_id: state.sessionId, persist: true });
    if (d.status !== "success") { pushChat("bot", "请求失败：" + errMsg(d)); return; }
    state.sessionId = d.session_id || state.sessionId;
    $("#ag-session").textContent = "会话 " + String(state.sessionId || "").slice(0, 8) + " · 引擎 " + (d.engine || "—");
    pushChat("bot", d.answer || "（无回答）");
    const intentPill = `<span class="pill info">意图 ${esc(d.intent || "unknown")}</span><span class="pill">置信度 ${fmt(d.intent_confidence, 2)}</span>`;
    let h = `<div class="ans-head">${intentPill}</div><div class="ans-body">${esc(d.answer || "")}</div>`;
    if (d.explain_chain && d.explain_chain.length) h += `<div class="card-title" style="margin-top:10px">决策链路</div>${d.explain_chain.map(x => `<div class="hint">· ${esc(x)}</div>`).join("")}`;
    if (d.tools_used && d.tools_used.length) h += `<div class="hint">工具：${esc(d.tools_used.join(", "))}</div>`;
    if (d.follow_up && d.follow_up.length) h += `<div class="hint">追问建议：${esc(d.follow_up.join(" / "))}</div>`;
    if (d.plan) h += `<div class="card-title" style="margin-top:10px">调度方案</div><pre class="ans-body mono">${esc(JSON.stringify({ mode: d.plan.mode, combination: d.plan.target_combination, power_kw: d.plan.estimated_power_kw, surge_status: d.plan.surge_status, execution_permission: d.plan.execution_permission }, null, 2))}</pre>`;
    if (d.tool_results && d.tool_results.knowledge_search) { const k = d.tool_results.knowledge_search; if (k.citations) h += citationList(k.citations); }
    $("#ag-result").innerHTML = h;
  }

  /* ------------------------------ 知识库 ------------------------------ */
  let kbBound = false;
  async function renderKnowledge() {
    if (!kbBound) {
      kbBound = true;
      $("#kb-ask").addEventListener("click", () => kbAsk($("#kb-input").value));
      $("#kb-search-btn").addEventListener("click", () => kbSearch($("#kb-input").value));
      $("#kb-input").addEventListener("keydown", e => { if (e.key === "Enter") kbAsk($("#kb-input").value); });
    }
    const st = await capi("/api/knowledge/status");
    if (st.status === "success") {
      $("#kb-badge").innerHTML = kbStatusPill(st);
      $("#kb-statusbar").innerHTML = `<span><b>状态</b>${kbStatusPill(st)}</span><span><b>文档</b>${st.n_docs}</span><span><b>知识块</b>${st.n_chunks}</span><span><b>版本</b>${esc(st.kb_version)}</span><span><b>构建时间</b>${esc(st.built_at || "—")}</span>`
        + (st.kb_status === "stale" ? `<span class="muted">知识来源已更新，索引等待重建</span>` : "");
      $("#kb-cats").innerHTML = (st.categories || []).map(c => `<span class="kb-cat">${esc(c)}</span>`).join("");
    } else { $("#kb-badge").innerHTML = '<span class="pill err">不可用</span>'; $("#kb-statusbar").innerHTML = `<span class="muted">${esc(errMsg(st))}</span>`; }
    if (!$("#kb-answer").dataset.done) $("#kb-answer").innerHTML = '<div class="muted">输入问题开始查询；知识解释与实时数值请分别查看对应页面。</div>';
  }
  async function kbAsk(q) {
    q = (q || "").trim(); if (!q) return;
    $("#kb-input").value = q; $("#kb-answer").innerHTML = '<div class="muted">检索中…</div>';
    const d = await post("/api/knowledge/ask", { question: q, top_k: 5 });
    delete $("#kb-answer").dataset.done;
    if (d.status !== "success" && d.status !== "no_answer") { $("#kb-answer").innerHTML = `<div class="warn-banner">${esc(errMsg(d))}</div>`; return; }
    const ok = d.status === "success";
    let h = `<div class="ans-head">${ok ? '<span class="pill ok">知识库回答</span>' : '<span class="pill warn">暂无可靠依据</span>'}`
      + (d.kb_status === "stale" ? '<span class="pill warn">索引已过期</span>' : "") + `</div>`;
    h += `<div class="ans-body">${esc(d.answer || "")}</div>`;
    if (ok) h += `<div class="hint">检索相关度 ${fmt((d.confidence || 0) * 100, 0)}% ｜ 耗时 ${fmt(d.elapsed_seconds, 3)}s ｜ 版本 ${esc(d.kb_version)}</div>`;
    h += citationList(d.citations);
    if (!ok) h += '<div class="hint">知识库无匹配来源，可尝试换一种问法或补充知识文档。</div>';
    $("#kb-answer").innerHTML = h;
  }
  async function kbSearch(q) {
    q = (q || "").trim(); if (!q) return;
    $("#kb-answer").innerHTML = '<div class="muted">检索中…</div>';
    const d = await post("/api/knowledge/search", { query: q, top_k: 5 });
    delete $("#kb-answer").dataset.done;
    if (d.status !== "success") { $("#kb-answer").innerHTML = `<div class="warn-banner">${esc(errMsg(d))}</div>`; return; }
    $("#kb-answer").innerHTML = `<div class="ans-head"><span class="pill info">检索结果</span></div>` + citationList(d.results || d.citations || []);
  }

  /* ------------------------------ 指标与验收 ------------------------------ */
  async function renderMetrics() {
    const d = await capi("/api/metrics/comparison");
    if (d.status !== "success") { $("#mt-forecast").innerHTML = `<div class="warn-banner">${esc(errMsg(d))}</div>`; return; }
    const fm = (d.forecast_metrics || {}).metrics || {};
    $("#mt-forecast").innerHTML = table([{ label: "时域", key: "h" }, { label: "MAPE", num: true, key: "m" }, { label: "MAE", num: true, key: "mae" }, { label: "RMSE", num: true, key: "r" }, { label: "WAPE", num: true, key: "w" }],
      [15, 30, 60].map(h => { const x = fm[h + "min"] || {}; return { h: h + " 分钟", m: pct(x.MAPE, 2), mae: fmt(x.MAE, 2), r: fmt(x.RMSE, 2), w: pct(x.WAPE, 2) }; }));
    const ld = d.latest_dispatch;
    $("#mt-dispatch").innerHTML = ld ? table([{ label: "项", key: "k" }, { label: "值", key: "v" }], Object.entries(ld).map(([k, v]) => ({ k: esc(k), v: esc(typeof v === "object" ? JSON.stringify(v) : v) })))
      : '<div class="muted">暂无最新调度对比数据（落库求解后可见）。</div>';
  }

  /* ------------------------------ 路由 ------------------------------ */
  const RENDER = { system: renderSystem, scada: renderScada, models: renderModels, dispatch: renderDispatch, control: renderControl, agent: renderAgent, knowledge: renderKnowledge, metrics: renderMetrics };
  function activate(t) {
    $$(".nav-item").forEach(n => n.classList.toggle("active", n.dataset.target === t));
    $$(".panel").forEach(p => p.classList.toggle("active", p.id === t));
    const m = PAGE_META[t] || ["", ""];
    $("#page-title").textContent = m[0]; $("#page-sub").textContent = m[1];
    if (t !== "scada") stopScada();
    const fn = RENDER[t]; if (fn) fn().catch(e => console.error(t, e));
  }
  $("#nav").addEventListener("click", e => { const i = e.target.closest(".nav-item"); if (i) activate(i.dataset.target); });

  async function health() {
    const r = await capi("/api/system/health");
    const ok = r.status === "success";
    $("#be-dot").className = "dot " + (ok ? "ok" : "bad");
    $("#be-text").textContent = ok ? "后端已连接（:8000）" : "后端未连接";
  }

  health().catch(() => { $("#be-dot").className = "dot bad"; $("#be-text").textContent = "后端未连接"; });
  renderSystem().catch(e => console.error(e));
})();
