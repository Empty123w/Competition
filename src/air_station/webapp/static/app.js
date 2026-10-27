/* 可视化平台前端逻辑：10 个页面 + 演示功能。 */
(function () {
  "use strict";
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const cache = {};
  const state = { times: [], idx: 0, timer: null, speed: 1000, currentAsOf: null, playing: false };

  const PAGE_META = {
    overview: ["运行总览", "系统运行状态、推荐方案与关键指标"],
    units: ["机组实时状态", "4#/5# 机组实时工况与趋势（支持模拟播放）"],
    forecast: ["用气负荷预测", "LightGBM 15/30/60 分钟点预测 + 90% 区间"],
    dispatch: ["智能调度方案", "人工/智能对比、优化前后、决策链路与方案确认"],
    health: ["设备健康中心", "无监督健康评分、风险等级与剩余寿命（模拟）"],
    surge: ["喘振风险监控", "代理喘振边界、当前评估与历史事件"],
    energy: ["能效与成本分析", "SEC、电量、成本与节电对比"],
    alarms: ["告警与运行事件", "健康告警、喘振代理告警与故障事件"],
    agent: ["Agent 自然语言交互", "自然语言下达调度目标并返回解释"],
    knowledge: ["知识中心", "基于项目资料的知识问答，区分知识解释与实时结果"],
    acceptance: ["测试验收", "统一测试集下的 14 项指标验收结果"],
  };
  const MODE_CN = { b3: "能效优先 (B3)", balanced: "低负荷优先", load_balanced: "加载率均衡 (LRB)" };
  const ACTION_CN = { start: ["启动", "tag-start"], stop: ["停机", "tag-stop"], adjust: ["调节", "tag-adjust"], hold: ["保持", "tag-hold"] };

  async function api(path, opts) {
    const res = await fetch(path, opts);
    if (!res.ok) throw new Error("API " + path + " -> " + res.status);
    return res.json();
  }
  function fmt(v, d) {
    if (v === null || v === undefined || (typeof v === "number" && !isFinite(v))) return "—";
    if (typeof v === "number") {
      if (Math.abs(v) >= 1000) return v.toLocaleString(undefined, { maximumFractionDigits: 0 });
      return Number(v.toFixed(d === undefined ? 2 : d)).toString();
    }
    return String(v);
  }
  function pct(v, d) { return (v === null || v === undefined) ? "—" : fmt(v, d === undefined ? 1 : d) + "%"; }
  function money(v, d) { return (v === null || v === undefined) ? "—" : "¥ " + fmt(v, d === undefined ? 0 : d); }
  function esc(s) { return String(s === null || s === undefined ? "" : s).replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }
  function table(cols, rows) {
    let h = "<table><thead><tr>";
    cols.forEach(c => { h += `<th class="${c.num ? "num" : ""}">${c.label}</th>`; });
    h += "</tr></thead><tbody>";
    rows.forEach(r => {
      h += "<tr>";
      cols.forEach(c => {
        const v = typeof c.value === "function" ? c.value(r) : r[c.key];
        h += `<td class="${c.num ? "num" : ""}">${v === null || v === undefined || v === "" ? "—" : v}</td>`;
      });
      h += "</tr>";
    });
    return h + "</tbody></table>";
  }
  function infoRows(pairs) {
    return pairs.map(p => `<div class="row"><span class="lbl">${p[0]}</span><span class="val">${p[1] === null || p[1] === undefined || p[1] === "" ? "—" : p[1]}</span></div>`).join("");
  }
  function kpi(label, value, unit, sub, accent) {
    return `<div class="kpi ${accent || ""}"><div class="k-label">${label}</div>
      <div class="k-value">${value}<span class="k-unit">${unit || ""}</span></div><div class="k-sub">${sub || ""}</div></div>`;
  }
  function riskBadge(level) {
    const lv = String(level || "unknown");
    const map = { low: "低", medium: "中", high: "高", unknown: "未知", normal: "正常", attention: "关注", warning: "预警", critical: "严重" };
    const cls = { low: "risk-low", medium: "risk-medium", high: "risk-high", normal: "risk-normal", attention: "risk-attention", warning: "risk-warning", critical: "risk-critical" }[lv] || "risk-unknown";
    return `<span class="pill ${cls}">${map[lv] || lv}</span>`;
  }
  function actionTag(a) {
    const m = ACTION_CN[a] || [a, "tag-hold"];
    return `<span class="action-tag ${m[1]}">${m[0]}</span>`;
  }
  function chips(items, clsFn) {
    return `<div class="chips">${items.map(x => `<span class="chip ${clsFn ? clsFn(x) : ""}">${esc(x)}</span>`).join("")}</div>`;
  }

  /* ============================ 1 运行总览 ============================ */
  async function renderOverview() {
    const ov = cache.overview || (cache.overview = await api("/api/overview"));
    const q = state.currentAsOf ? "/api/rt/state?as_of=" + encodeURIComponent(state.currentAsOf) : "/api/rt/state";
    const rt = cache.rtLatest || (cache.rtLatest = await api(q));
    state.currentAsOf = state.currentAsOf || rt.as_of;
    const k = ov.kpi || {}, cl = k.closed_loop || {}, fc = ov.forecast_metrics || {}, rm = k.rul_metrics || {};
    $("#ov-kpis").innerHTML = [
      kpi("预测 MAPE · 15min", fmt(k.forecast_mape_15, 2), " %", `目标 ≤ ${fmt(k.forecast_target_mape, 1)}%`, "accent-blue"),
      kpi("4# 健康评分", fmt((k.health_scores || {})["4#"], 0), " 分", "等级: " + fmt((k.health_risk || {})["4#"]), "accent-teal"),
      kpi("5# 健康评分", fmt((k.health_scores || {})["5#"], 0), " 分", "等级: " + fmt((k.health_risk || {})["5#"]), "accent-teal"),
      kpi("闭环节能 (normal/B3)", fmt(cl.sec_reduction_pct, 1), " %", "vs 人工基线 (仿真)", "accent-purple"),
      kpi("压力合格率 (sim)", fmt((cl.pressure_compliance_b3 || 0) * 100, 0), " %", "工程假设闭环", "accent-amber"),
      kpi("RUL within 20%", fmt((rm.within_20pct || 0) * 100, 1), " %", "模拟退化模型", "accent-blue"),
    ].join("");

    $("#ov-flow").innerHTML = [
      ["原始运行数据", "设备/管网/负荷/环境/事件/维护（184 天，1 分钟）", "done"],
      ["数据校验与标准化", "A1/A2：字段校验、清洗、站级与设备级时序"],
      ["负荷预测 · 健康诊断", "A4/A5：LightGBM 预测 + 无监督健康评分"],
      ["历史基线 + 优化调度 + 安全约束", "B1/B3/B4：多机组合、能耗目标、喘振代理"],
      ["API / MCP / Agent 协同", "统一接口与自然语言调度（本页面）"],
      ["闭环仿真执行反馈", "压力—供需—能耗闭环（工程假设）"],
    ].map((n, i, arr) => {
      const node = `<div class="flow-node ${n[2] || ""}"><div class="fn-t">${n[0]}</div><div class="fn-d">${n[1]}</div></div>`;
      return i < arr.length - 1 ? node + '<div class="flow-arrow">↓</div>' : node;
    }).join("");

    const rec = rt.recommendation || {};
    $("#ov-reco").innerHTML =
      `<div class="snapshot">
        <div class="snap-item"><div class="s-l">当前时刻</div><div class="s-v" style="font-size:14px">${esc(rt.as_of)}</div></div>
        <div class="snap-item"><div class="s-l">母管压力</div><div class="s-v">${fmt(rt.station.header_pressure_mpa, 3)} MPa</div></div>
        <div class="snap-item"><div class="s-l">推荐模式</div><div class="s-v" style="font-size:14px">${rec.mode ? MODE_CN[rec.mode] : "—"}</div></div>
        <div class="snap-item"><div class="s-l">推荐功率</div><div class="s-v">${fmt(rec.power_kw, 0)} kW</div></div>
      </div>`;

    const fm = fc.metrics || {};
    $("#ov-table").innerHTML = table(
      [{ label: "模块", key: "m" }, { label: "关键指标", key: "n" }, { label: "数值", num: true, key: "v" }],
      [
        { m: "负荷预测", n: "MAPE 15/30/60min", v: `${fmt((fm["15min"] || {}).MAPE, 2)} / ${fmt((fm["30min"] || {}).MAPE, 2)} / ${fmt((fm["60min"] || {}).MAPE, 2)} %` },
        { m: "预测区间", n: "90% 覆盖率", v: pct(((fc.confidence_interval || {}).test_coverage_rate || 0) * 100, 2) },
        { m: "健康诊断", n: "最新评分 4#/5#", v: `${fmt((k.health_scores || {})["4#"], 0)} / ${fmt((k.health_scores || {})["5#"], 0)}` },
        { m: "RUL(模拟)", n: "MAE", v: fmt(rm.MAE, 1) + " 小时" },
        { m: "场景仿真", n: "B3 SEC 降幅", v: pct(cl.sec_reduction_pct, 1) },
      ]);

    const st = await api("/api/station");
    const labels = st.time.map(t => t.slice(5));
    Charts.line($("#ov-pressure"), { labels, yMin: 0.5, yMax: 0.7, series: [{ name: "母管压力 (MPa)", color: "#2F6BFF", data: st.header_pressure_mpa }] });
    Charts.line($("#ov-flow-chart"), { labels, series: [{ name: "总流量 (m³/min)", color: "#00B3A4", data: st.total_flow_m3_min }] });
    renderKnowledgeCard();
  }

  async function renderKnowledgeCard() {
    const el = $("#ov-knowledge");
    if (!el) return;
    try {
      const st = await api("/api/knowledge/status");
      el.innerHTML = knowledgeCard(st);
      const go = $("#ov-kb-go");
      if (go) go.addEventListener("click", () => activate("knowledge"));
    } catch (e) {
      el.innerHTML = `<div class="pill risk-high">知识库不可用</div> <span class="muted">${esc(e.message)}</span>`;
    }
  }

  /* ============================ 2 机组实时状态 ============================ */
  function renderUnitsFrame(frame) {
    const s = frame.state;
    $("#units-time").textContent = "@ " + s.as_of;
    const devs = s.devices || {};
    $("#units-cards").innerHTML = ["4#", "5#"].map(d => {
      const x = devs[d] || {};
      const lr = x.flow_m3_min != null && x.running ? null : (x.igv_percent);
      const load = x.load_ratio;
      return `<div class="unit-card ${x.running ? "running" : "stopped"}">
        <div class="unit-head"><span class="unit-name">${d} 空压机</span>${x.running ? '<span class="pill risk-low">运行</span>' : '<span class="pill risk-unknown">停机</span>'}</div>
        <div class="unit-metrics">
          <span class="um-l">流量</span><span class="um-v">${fmt(x.flow_m3_min, 1)} m³/min</span>
          <span class="um-l">功率</span><span class="um-v">${fmt(x.power_kw, 0)} kW</span>
          <span class="um-l">排气压力</span><span class="um-v">${fmt(x.discharge_pressure_mpa, 3)} MPa</span>
          <span class="um-l">IGV</span><span class="um-v">${fmt(x.igv_percent, 1)} %</span>
          <span class="um-l">BOV 闭度</span><span class="um-v">${fmt(x.bov_closed_percent, 1)} %</span>
          <span class="um-l">数据有效</span><span class="um-v">${x.quality_valid ? "是" : "否"}</span>
        </div>
        <div style="margin-top:10px"><span class="um-l muted">负荷率 ${fmt(load, 2)}</span>
          <div class="mbar"><i style="width:${Math.max(0, Math.min(1, load || 0)) * 100}%"></i></div></div>
      </div>`;
    }).join("");
    const h = s.history || {};
    const labels = (h.labels || []).map(t => String(t).slice(11, 16));
    Charts.line($("#units-flow"), { labels, series: [
      { name: "4# 流量", color: "#2F6BFF", data: (h.devices || {})["4#"] ? h.devices["4#"].flow : [] },
      { name: "5# 流量", color: "#B44DFF", data: (h.devices || {})["5#"] ? h.devices["5#"].flow : [] }] });
    Charts.line($("#units-power"), { labels, series: [
      { name: "4# 功率", color: "#2F6BFF", data: (h.devices || {})["4#"] ? h.devices["4#"].power : [] },
      { name: "5# 功率", color: "#B44DFF", data: (h.devices || {})["5#"] ? h.devices["5#"].power : [] }] });
    Charts.line($("#units-load"), { labels, yMin: 0, yMax: 1.1, series: [
      { name: "4# 负荷率", color: "#2F6BFF", data: (h.devices || {})["4#"] ? h.devices["4#"].load : [] },
      { name: "5# 负荷率", color: "#B44DFF", data: (h.devices || {})["5#"] ? h.devices["5#"].load : [] }] });
  }
  async function renderUnits() {
    const i = state.idx;
    const frame = cache["frame:" + i] || (cache["frame:" + i] = await api("/api/rt/frame?i=" + i));
    renderUnitsFrame(frame);
  }

  /* ============================ 3 用气负荷预测 ============================ */
  async function renderForecast() {
    const horizon = $("#fc-horizon").value;
    if (!$("#fc-horizon").dataset.bound) {
      $("#fc-horizon").dataset.bound = "1";
      $("#fc-horizon").addEventListener("change", () => { delete cache["forecast"]; renderForecast(); });
    }
    const data = cache["forecast"] || (cache["forecast"] = await api("/api/forecast?horizon=" + horizon));
    const live = data.live || {}, preds = live.predictions || [];
    $("#fc-live").innerHTML = preds.length ? preds.map(p =>
      `<div class="fc-card"><div class="h">${p.horizon_minutes} 分钟 · ${esc(String(p.target_time || "").replace("T", " ").slice(5, 16))}</div>
       <div class="v">${fmt(p.predicted_flow, 2)}</div><div class="b">区间 ${fmt(p.lower_bound, 1)} ~ ${fmt(p.upper_bound, 1)} m³/min</div></div>`).join("")
      : `<div class="muted">实时预测不可用：${esc(live.message || "未知")}</div>`;
    const s = data.series || {};
    const labels = (s.time || []).map(t => t.slice(5, 16));
    Charts.line($("#fc-chart"), { labels, band: { lower: s.lower_bound || [], upper: s.upper_bound || [], color: "rgba(47,107,255,0.13)" },
      series: [        { name: "预测流量", color: "#2F6BFF", data: s.predicted_flow || [] }, { name: "实测流量", color: "#E5484D", data: s.actual_flow || [], width: 1.6 }] });
    const fm = (data.metrics || {}).metrics || {};
    $("#fc-metrics").innerHTML = table(
      [{ label: "时域", key: "h" }, { label: "MAPE", num: true, key: "mape" }, { label: "MAE", num: true, key: "mae" },
       { label: "RMSE", num: true, key: "rmse" }, { label: "WAPE", num: true, key: "wape" }, { label: "最优基线", key: "b" }],
      [15, 30, 60].map(h => { const x = fm[h + "min"] || {}; return { h: h + " 分钟", mape: pct(x.MAPE, 2), mae: fmt(x.MAE, 2), rmse: fmt(x.RMSE, 2), wape: pct(x.WAPE, 2), b: x.baseline_best || "—" }; }));
    const imp = (data.metrics || {}).feature_importance_top5 || [];
    Charts.bar($("#fc-importance"), { items: imp.map(i => ({ label: i.feature, value: i.importance })) });
  }

  /* ============================ 4 智能调度方案 ============================ */
  let dpBound = false;
  async function renderDispatch() {
    if (!dpBound) {
      dpBound = true;
      $("#dp-run").addEventListener("click", runDispatch);
      $("#dp-modes").addEventListener("click", onModeAction);
    }
    if (!$("#dp-time").options.length) {
      const t = await api("/api/dispatch/times");
      state.times = t.times || [];
      $("#dp-time").innerHTML = state.times.map(x => `<option value="${x}">${x}</option>`).join("");
      if (state.currentAsOf) $("#dp-time").value = state.currentAsOf;
    }
    if (!$("#dp-modes").dataset.done) runDispatch();
  }
  async function runDispatch() {
    const asOf = $("#dp-time").value || state.currentAsOf;
    const btn = $("#dp-run"); btn.disabled = true; btn.textContent = "求解中…";
    $("#dp-status").textContent = "求解中…";
    $("#loading").classList.remove("hidden");
    try {
      const data = await api("/api/dispatch/compare?as_of=" + encodeURIComponent(asOf));
      const any = data.b3 || data.balanced || data.load_balanced;
      if (any && any.snapshot) renderSnapshot(any.snapshot, any);
      renderModeCards(data);
      renderCompare(data);
      renderLogic((data.b3 || {}).result || {});
      Charts.bar($("#dp-latency"), { items: ["b3", "balanced", "load_balanced"].map(m => ({ label: MODE_CN[m], value: ((data[m] || {}).result || {}).latency_ms, color: m === "b3" ? "#2F6BFF" : m === "balanced" ? "#00B3A4" : "#FF8A3D" })) });
      $("#dp-modes").dataset.done = "1";
      state.currentAsOf = asOf;
      const lats = ["b3", "balanced", "load_balanced"].map(m => ((data[m] || {}).result || {}).latency_ms).filter(v => v != null);
      $("#dp-status").textContent = `已求解 ${asOf} · 3 模式 · 最长 ${fmt(Math.max(...lats), 0)} ms`;
      flash($("#dp-modes"));
      $("#dp-modes").scrollIntoView({ behavior: "smooth", block: "nearest" });
      await renderControl();
    } catch (e) {
      $("#dp-status").textContent = "求解失败";
      $("#dp-modes").innerHTML = `<div class="warn-banner">调度求解失败：${esc(e.message)}</div>`;
    } finally {
      btn.disabled = false; btn.textContent = "对三种模式求解"; $("#loading").classList.add("hidden");
    }
  }
  function flash(el) {
    if (!el) return;
    el.classList.remove("flash");
    void el.offsetWidth;
    el.classList.add("flash");
    setTimeout(() => el.classList.remove("flash"), 900);
  }
  function renderSnapshot(snap, pack) {
    const fr = pack.forecast || {};
    let h = `<div class="snap-item"><div class="s-l">决策时刻 as_of</div><div class="s-v" style="font-size:14px">${esc(pack.as_of)}</div></div>`;
    h += `<div class="snap-item"><div class="s-l">母管压力</div><div class="s-v">${fmt(snap.header_pressure_mpa, 3)} MPa</div></div>`;
    (snap.devices || []).forEach(d => { h += `<div class="snap-item"><div class="s-l">${d.device_id} ${d.running ? "运行" : "停机"}</div><div class="s-v">${fmt(d.flow_m3_min, 1)} m³/min</div></div>`; });
    h += `<div class="snap-item"><div class="s-l">15min 预测上界</div><div class="s-v">${fmt(fr.upper_bound, 1)} m³/min</div></div>`;
    $("#dp-snapshot").innerHTML = h;
  }
  function renderModeCards(data) {
    $("#dp-modes").innerHTML = ["b3", "balanced", "load_balanced"].map(m => {
      const r = (data[m] || {}).result || {};
      const cls = r.status === "success" && !r.is_fallback ? "ok" : (r.is_fallback ? "fb" : "err");
      const pill = r.status === "success" && !r.is_fallback ? '<span class="pill ok">求解成功</span>' : (r.is_fallback ? '<span class="pill fb">安全回退</span>' : `<span class="pill err">${esc(r.status || "错误")}</span>`);
      const acts = (r.device_actions || []).map(a => `<div class="act"><span>${a.device_id} ${actionTag(a.action)}</span><span>${fmt(a.target_flow_m3_min, 1)} m³/min · ${fmt(a.estimated_power_kw, 0)} kW</span></div>`).join("");
      const blocked = r.safety_blocked ? '<span class="pill risk-high">安全阻断</span>' : "";
      return `<div class="mode-card ${cls}" data-mode="${m}">
        <div class="mode-head"><span class="mode-name">${MODE_CN[m]}</span><span>${pill}${blocked}</span></div>
        <div class="mode-metrics">
          <span class="mm-l">机组组合</span><span class="mm-v">${(r.target_combination || []).join(" + ") || "—"}</span>
          <span class="mm-l">供气</span><span class="mm-v">${fmt(r.total_flow_m3_min, 1)} m³/min</span>
          <span class="mm-l">预计功率</span><span class="mm-v">${fmt(r.estimated_power_kw, 0)} kW</span>
          <span class="mm-l">喘振状态</span><span class="mm-v">${riskBadge(r.surge_status === "not_assessed_b4" ? "unknown" : r.surge_status)}</span>
          <span class="mm-l">时延</span><span class="mm-v">${fmt(r.latency_ms, 0)} ms</span>
        </div>
        <div class="actions">${acts || "<span class='muted'>无机组动作</span>"}</div>
        <div class="mode-actions" style="margin-top:10px;display:flex;gap:6px">
          <button class="btn btn-sm" data-act="confirm">确认</button>
          <button class="btn btn-sm btn-danger" data-act="reject">拒绝</button>
          <button class="btn btn-sm btn-ghost" data-act="fallback">回退 B1</button>
        </div>
      </div>`;
    }).join("");
  }
  function onModeAction(e) {
    const b = e.target.closest("button[data-act]");
    if (!b) return;
    const card = b.closest(".mode-card"); const mode = card.dataset.mode;
    api("/api/control/decide", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: mode, as_of: state.currentAsOf, action: b.dataset.act }) })
      .then(() => { delete cache["clog"]; renderControl(); })
      .catch(err => alert("记录失败：" + err.message));
  }
  function renderCompare(data) {
    const b3 = (data.b3 || {}).result || {};
    const bc = b3.baseline_comparison || {};
    const b1 = bc.baseline || {};
    const rows = [
      { k: "策略", b: "人工规则基线 (B1)", o: "智能调度 (B3)" },
      { k: "机组组合", b: (b1.combination || []).join(" + ") || "—", o: (b3.target_combination || []).join(" + ") || "—" },
      { k: "供气 (m³/min)", b: fmt(b1.total_flow_m3_min, 1), o: fmt(b3.total_flow_m3_min, 1) },
      { k: "预计功率 (kW)", b: fmt(b1.estimated_power_kw, 0), o: fmt(bc.optimized_power_kw ?? b3.estimated_power_kw, 0) },
      { k: "功率变化", b: "—", o: bc.power_reduction_pct != null ? '<span class="good">' + pct(bc.power_reduction_pct, 2) + " ↓</span>" : "—" },
    ];
    $("#dp-compare").innerHTML = table([{ label: "对比项", key: "k" }, { label: "人工调度", key: "b" }, { label: "智能调度", key: "o" }], rows);
  }
  function renderBeforeAfter(data) { /* handled in runDispatch via renderCompare + pressure */ }
  function renderLogic(r) {
    const steps = [];
    steps.push(["输入工况", `as_of 快照 + 15 分钟预测 + 健康评估`, []]);
    steps.push(["需求判定", `按供气上界口径取需求 ${fmt(r.required_flow_m3_min, 1)} m³/min`, []]);
    steps.push(["候选组合枚举", `停机 / 仅 4# / 仅 5# / 双机 → 最优组合 ${(r.target_combination || []).join(" + ") || "—"}`, []]);
    const oc = r.objective_components || {};
    const terms = oc.terms || {};
    steps.push(["目标函数", `加权归一化目标 total = ${fmt(oc.total_objective, 4)}`,
      Object.keys(terms).map(k => `${k}: ${fmt(terms[k], 4)}`).concat(Object.entries(oc.weights || {}).map(([k, v]) => `w_${k}=${v}`))]);
    const cons = r.constraints || [];
    steps.push(["约束校验", cons.map(c => `${c.name}: ${c.pass ? "通过" : "不通过"}`).join("；"),
      cons.map(c => (c.pass ? "✓ " : "✗ ") + c.name)]);
    const tp = r.transition_plan || [];
    steps.push(["切换过渡", tp.length ? `过渡步骤 ${tp.length} 步` : "无需切换", tp.map(s => `step:${s.step || ""} ${s.device_id || ""}`.trim())]);
    const sa = r.surge_assessment || {};
    const sdev = sa.devices || [];
    steps.push(["喘振评估", `整体状态 ${riskBadge(r.surge_status === "not_assessed_b4" ? "unknown" : r.surge_status)}`,
      sdev.map(d => `${d.device_id}: ${d.risk_level} (裕度 ${fmt(d.risk_margin, 3)})`)]);
    const rc = r.reason_codes || [];
    steps.push(["输出与原因码", `执行权限 ${esc(r.execution_permission || "—")}${r.is_fallback ? "；发生安全回退" : ""}`, rc]);

    $("#dp-logic").innerHTML = steps.map((s, i) =>
      `<div class="logic-step"><div class="logic-idx">${i + 1}</div><div class="logic-body">
        <div class="logic-t">${s[0]}</div><div class="logic-d">${s[1]}</div>
        ${s[2] && s[2].length ? chips(s[2], x => (String(x).startsWith("✓") || !/✗/.test(x) && /通过|✓/.test(x)) ? "pass" : (String(x).startsWith("✗") ? "fail" : "")) : ""}
      </div></div>`).join("");
  }
  async function renderPressureCompare() {
    const demo = cache["demo:normal"] || (cache["demo:normal"] = await api("/api/scenario/normal"));
    const c = demo.comparison || {};
    const b1 = demo.series.b1 || {}, b3 = demo.series.b3 || {};
    const el = $("#dp-beforeafter"); if (!el) return;
    el.innerHTML = `<div class="grid-2" style="gap:10px">
      <div class="cmp-card"><div class="c-h">能耗 (kWh) 人工 → 智能</div><div class="c-v">${fmt(c.energy_kwh_b1, 0)} → <span class="good">${fmt(c.energy_kwh_b3, 0)}</span></div></div>
      <div class="cmp-card"><div class="c-h">成本 (元) 人工 → 智能</div><div class="c-v">${money(c.cost_b1)} → <span class="good">${money(c.cost_b3)}</span></div></div>
      <div class="cmp-card"><div class="c-h">压力合格率 人工 → 智能</div><div class="c-v">${pct((c.pressure_compliance_b1 || 0) * 100, 0)} → <span class="good">${pct((c.pressure_compliance_b3 || 0) * 100, 0)}</span></div></div>
      <div class="cmp-card"><div class="c-h">压力波动降幅</div><div class="c-v good">${pct(c.pressure_std_reduction_pct, 1)}</div></div>
    </div><div class="hint">压力对比来自闭环仿真场景 normal（工程假设）；能耗/成本为同供气口径估算，电价按 ¥0.65/kWh（工业假设）。</div>`;
    const labels = (b1.time || []).map(t => String(t).slice(11, 16));
    Charts.line($("#dp-cost-chart"), { labels, yMin: 0.5, yMax: 0.7, series: [
      { name: "人工基线压力", color: "#E5484D", data: b1.pressure || [] },
      { name: "智能调度压力", color: "#2F6BFF", data: b3.pressure || [] }] });
  }

  /* ============================ 5 设备健康中心 ============================ */
  async function renderHealth() {
    const data = cache["health"] || (cache["health"] = await api("/api/health"));
    const devices = {}; (data.live.devices || []).forEach(d => { devices[d.device_id] = d; });
    ["4#", "5#"].forEach(d => {
      const info = devices[d] || {};
      const hs = info.health_score ?? 0;
      Charts.gauge($("#hl-gauge-" + d[0]), { value: hs, max: 100, label: info.risk_level || "", sub: "占比：" + fmt(hs, 1) + "%" });
      $("#hl-info-" + d[0]).innerHTML = infoRows([["风险等级", riskBadge(info.risk_level)], ["异常分", info.anomaly_score], ["主因", info.top_anomaly_factor], ["当前值", info.current_value], ["正常范围", info.normal_range], ["趋势", info.trend_direction], ["建议", info.suggestion]]);
    });
    const hist = data.history || {}, h4 = hist["4#"] || {}, h5 = hist["5#"] || {};
    Charts.line($("#hl-history"), { labels: (h4.time || []).map(t => t.slice(5, 16)), yMin: 0, yMax: 100, halo: true, series: [
      { name: "4# 健康评分", color: "#2F6BFF", data: h4.health_score || [], width: 2.2 }, { name: "5# 健康评分", color: "#B44DFF", data: h5.health_score || [], width: 2.2 }] });
    const hm = data.metrics || {};
    $("#hl-metrics").innerHTML = table([{ label: "设备", key: "d" }, { label: "最新评分", num: true, key: "s" }, { label: "风险", key: "r" }, { label: "均值", num: true, key: "mean" }, { label: "异常率", num: true, key: "ar" }, { label: "弱标签分离度", num: true, key: "sep" }],
      ["4#", "5#"].map(d => { const x = (hm.devices || {})[d] || {}; return { d, s: fmt(x.health_score_latest, 0), r: riskBadge(x.risk_level_latest), mean: fmt(x.health_score_mean, 2), ar: pct((x.anomaly_rate || 0) * 100, 2), sep: fmt(x.separation, 2) }; }));

    const rul = cache["rul"] || (cache["rul"] = await api("/api/rul"));
    const devs = (rul.live || {}).devices || [];
    Charts.bar($("#rul-bars"), { items: devs.flatMap(d => [{ label: d.device_id + " 估计", value: d.estimated_rul_hours, color: "#B44DFF" }, { label: d.device_id + " 下界", value: d.lower_bound_hours, color: "#C9A6F0" }, { label: d.device_id + " 上界", value: d.upper_bound_hours, color: "#E3D3F7" }]) });
    $("#rul-live").innerHTML = devs.map(d => infoRows([[d.device_id + " 估计 RUL", fmt(d.estimated_rul_hours, 1) + " 小时"], ["区间", fmt(d.lower_bound_hours, 1) + " ~ " + fmt(d.upper_bound_hours, 1) + " 小时"], ["方法", d.method]])).join("");
  }

  /* ============================ 6 喘振风险监控 ============================ */
  async function renderSurge() {
    const data = cache["surge"] || (cache["surge"] = await api("/api/surge"));
    const mon = data.monitor || {}, curve = mon.curve || {};
    const devices = Object.keys(curve);
    let labels = [];
    devices.forEach(d => { (curve[d].pressure || []).forEach(p => { if (!labels.includes(fmt(p, 3))) labels.push(fmt(p, 3)); }); });
    labels.sort((a, b) => parseFloat(a) - parseFloat(b));
    const series = devices.map((d, i) => ({ name: d + " 代理边界流量", color: i === 0 ? "#E5484D" : "#FF8A3D", data: curve[d].flow || [] }));
    Charts.line($("#sg-curve"), { labels: (curve[devices[0]] || {}).pressure ? curve[devices[0]].pressure.map(p => fmt(p, 3)) : [], series });

    const cur = data.current || {}, sa = cur.assessment || {};
    const dev = sa.devices || [];
    $("#sg-current").innerHTML = `<div class="hint">评估时刻 ${esc(cur.as_of)} · 模式 ${MODE_CN[cur.mode] || cur.mode} · 整体 ${riskBadge(cur.surge_status === "not_assessed_b4" ? "unknown" : cur.surge_status)}${cur.safety_blocked ? ' · <span class="pill risk-high">安全阻断</span>' : ""}</div>` +
      table([{ label: "设备", key: "d" }, { label: "风险", key: "r" }, { label: "裕度", num: true, key: "m" }, { label: "计划流量", num: true, key: "f" }, { label: "边界流量", num: true, key: "b" }, { label: "触发因素", key: "t" }],
        dev.map(d => ({ d: d.device_id, r: riskBadge(d.risk_level), m: fmt(d.risk_margin, 3), f: fmt(d.planned_flow_m3min, 1), b: fmt(d.boundary_flow_m3min, 1), t: (d.trigger_factors || []).join(", ") || "—" })));

    const st = mon.stats || {}, valid = st.valid || {};
    $("#sg-stats").innerHTML = table([{ label: "设备", key: "d" }, { label: "低", num: true, key: "l" }, { label: "中", num: true, key: "m" }, { label: "高", num: true, key: "h" }, { label: "未知", num: true, key: "u" }, { label: "代理事件", num: true, key: "e" }],
      Object.keys(valid).map(d => { const x = valid[d] || {}, lm = x.level_minutes || {}; return { d, l: lm.low, m: lm.medium, h: lm.high, u: lm.unknown, e: x.proxy_events }; })) +
      `<div class="hint">${esc(mon.real_label_note || "")}</div>`;
  }

  /* ============================ 7 能效与成本分析 ============================ */
  async function renderEnergy() {
    const data = cache["energy"] || (cache["energy"] = await api("/api/energy"));
    const segs = data.segments || {};
    const segKeys = Object.keys(segs);
    const seg = segs.valid ? "valid" : segKeys[0];
    const rows = segs[seg] ? Object.values(segs[seg].rows) : [];
    Charts.bar($("#en-sec"), { items: rows.map((r, i) => ({ label: r.name, value: r.SEC, color: ["#6B7A8F", "#2F6BFF", "#00B3A4", "#FF8A3D"][i % 4] })) });
    Charts.bar($("#en-cost"), { items: rows.map((r, i) => ({ label: r.name, value: r.cost_cny, color: ["#6B7A8F", "#2F6BFF", "#00B3A4", "#FF8A3D"][i % 4] })) });
    $("#en-table").innerHTML = `<div class="hint">口径：${esc(seg)}（${(segs[seg].scope)}，n=${segs[seg].n_steps}）；电价 ${money(data.price_cny_per_kwh, 2)}/kWh（${esc(data.price_note)}）</div>` +
      table([{ label: "策略", key: "n" }, { label: "电量(kWh)", num: true, key: "e" }, { label: "SEC", num: true, key: "s" }, { label: "成本(元)", num: true, key: "c" }, { label: "SEC降幅", num: true, key: "sr" }, { label: "低负荷降幅", num: true, key: "lr" }],
        rows.map(r => ({ n: r.name, e: fmt(r.E_kwh, 0), s: fmt(r.SEC, 4), c: fmt(r.cost_cny, 0), sr: r.sec_reduction_pct != null ? pct(r.sec_reduction_pct, 2) : "—", lr: r.low_reduction_pct != null ? pct(r.low_reduction_pct, 1) : "—" })));

    const sim = cache["sim"] || (cache["sim"] = await api("/api/simulation"));
    const summary = sim.summary || {};
    const rr = [];
    Object.entries(summary.scenarios || {}).forEach(([sc, byStrat]) => {
      const b1 = byStrat.b1 || {}, b3 = byStrat.b3 || {};
      rr.push({ sc, sec: fmt(b3.sec_kwh_per_m3, 4), red: pct(b3.sec_reduction_vs_b1_pct, 2), comp: pct((b3.pressure_compliance || 0) * 100, 0), std: pct(b3.pressure_std_reduction_vs_b1_pct, 1) });
    });
    $("#en-closed").innerHTML = table([{ label: "场景", key: "sc" }, { label: "B3 SEC", num: true, key: "sec" }, { label: "SEC 降幅", num: true, key: "red" }, { label: "压力合格率", num: true, key: "comp" }, { label: "压力波动降幅", num: true, key: "std" }], rr);
  }

  /* ============================ 8 告警与运行事件 ============================ */
  async function renderAlarms() {
    const data = cache["alarms"] || (cache["alarms"] = await api("/api/alarms"));
    $("#al-health").innerHTML = table([{ label: "设备", key: "d" }, { label: "风险等级", key: "r" }, { label: "健康评分", num: true, key: "s" }],
      (data.health || []).map(h => ({ d: h.device_id, r: riskBadge(h.risk), s: fmt(h.score, 0) })));
    const ss = data.surge_stats || {};
    $("#al-surge").innerHTML = table([{ label: "设备", key: "d" }, { label: "高风险分钟", num: true, key: "h" }, { label: "未知分钟", num: true, key: "u" }, { label: "代理事件", num: true, key: "e" }],
      Object.keys(ss).map(d => { const x = ss[d] || {}, lm = x.level_minutes || {}; return { d, h: lm.high, u: lm.unknown, e: x.proxy_events }; })) + `<div class="hint">${esc(data.surge_note || "")}</div>`;
    $("#al-table").innerHTML = table([
      { label: "设备", key: "d" }, { label: "开始", key: "s" }, { label: "结束", key: "e" }, { label: "时长(min)", num: true, key: "m" },
      { label: "故障码", key: "c" }, { label: "标签质量", key: "q" },
      { label: "站级共因", key: "sl", value: r => r.station_level ? '<span class="pill risk-medium">是</span>' : "否" }],
      (data.fault_episodes || []).slice(0, 40).map(x => ({ ...x, d: x.device_id, s: x.start, e: x.end, m: x.minutes, c: x.code, q: x.quality })));
    $("#al-note").textContent = `故障事件共 ${data.fault_total} 段；故障码 26：${(data.fault_codes || {})[26] || "弱标签"}`;
  }

  /* ============================ 9 Agent 交互 ============================ */
  let agBound = false;
  async function renderAgent() {
    if (!agBound) {
      agBound = true;
      const intents = (await api("/api/agent/intents")).intents || [];
      $("#ag-quick").innerHTML = intents.map(i => `<button data-msg="${esc(i.keywords[0])}">${esc(i.keywords[0])}</button>`).join("") +
        ["查看预测", "查看健康", "剩余寿命", "喘振风险"].map(t => `<button data-msg="${t}">${t}</button>`).join("");
      $("#ag-quick").addEventListener("click", e => { const b = e.target.closest("button"); if (b) sendAgent(b.dataset.msg); });
      $("#ag-send").addEventListener("click", () => sendAgent($("#ag-input").value));
      $("#ag-input").addEventListener("keydown", e => { if (e.key === "Enter") sendAgent($("#ag-input").value); });
      pushChat("bot", "你好，请描述调度目标或查询内容，例如“优先省电”“减少大马拉小车”“查看预测”。");
    }
  }
  function pushChat(who, text) {
    const d = document.createElement("div"); d.className = "msg " + who; d.textContent = text;
    $("#ag-chat").appendChild(d); $("#ag-chat").scrollTop = $("#ag-chat").scrollHeight;
  }
  async function sendAgent(msg) {
    if (!msg || !msg.trim()) return;
    pushChat("user", msg); $("#ag-input").value = "";
    try {
      const r = await api("/api/agent/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: msg, as_of: state.currentAsOf }) });
      pushChat("bot", r.reply || "已返回结果。");
      renderAgentResult(r);
    } catch (e) { pushChat("bot", "出错：" + e.message); }
  }
  function renderAgentResult(r) {
    const kbNoAnswer = r.knowledge && r.knowledge.status !== "success";
    const badge = (r.answer_type === "knowledge" && kbNoAnswer) ? '<span class="pill risk-medium">暂无可靠依据</span>' : answerTypeBadge(r.answer_type);
    let h = `<div class="ans-head">${badge}`;
    if (r.mode) h += `<span class="pill risk-unknown">${esc(MODE_CN[r.mode] || r.mode)}</span>`;
    h += `</div>`;
    if (r.sections && r.sections.length) {
      h += r.sections.map(s => `<div class="ans-sec"><div class="ans-sec-t">${esc(s.title)}</div><div class="ans-sec-c">${esc(s.text)}</div></div>`).join("");
    } else if (r.reply) {
      h += `<div class="ans-body">${esc(r.reply)}</div>`;
    }
    if (r.knowledge && r.knowledge.status === "success") {
      h += `<div class="hint">检索相关度 ${fmt((r.knowledge.confidence || 0) * 100, 0)}% ｜ 查询耗时 ${fmt(r.knowledge.elapsed_seconds || 0, 3)} s ${evidenceTag(r.knowledge.validation_mode)}</div>`;
    }
    if (r.forecast) h += forecastMini(r.forecast);
    if (r.health) h += healthMini(r.health);
    if (r.rul) h += table([{ label: "设备", key: "d" }, { label: "估计(h)", num: true, key: "v" }, { label: "区间(h)", key: "r" }], (r.rul.devices || []).map(d => ({ d: d.device_id, v: fmt(d.estimated_rul_hours, 1), r: `${fmt(d.lower_bound_hours, 1)} ~ ${fmt(d.upper_bound_hours, 1)}` })));
    if (r.surge) h += table([{ label: "设备", key: "d" }, { label: "风险", key: "r" }], ((r.surge.assessment || {}).devices || []).map(d => ({ d: d.device_id, r: riskBadge(d.risk_level) })));
    if (r.dispatch) h += `<div class="hint">模式：${MODE_CN[r.mode] || r.mode} · as_of ${esc(r.dispatch.as_of)}</div>` + modeMini(r.dispatch.result || {});
    if (r.citations && r.citations.length) h += citationList(r.citations);
    $("#ag-result").innerHTML = h;
  }
  function modeMini(r) {
    return table([{ label: "项", key: "k" }, { label: "值", key: "v" }], [
      { k: "状态", v: r.status + (r.is_fallback ? "（回退）" : "") }, { k: "组合", v: (r.target_combination || []).join(" + ") },
      { k: "供气", v: fmt(r.total_flow_m3_min, 1) + " m³/min" }, { k: "功率", v: fmt(r.estimated_power_kw, 0) + " kW" },
      { k: "喘振", v: riskBadge(r.surge_status === "not_assessed_b4" ? "unknown" : r.surge_status) }, { k: "执行权限", v: r.execution_permission }]);
  }
  function forecastMini(fc) {
    return table([{ label: "时域", key: "h" }, { label: "预测", num: true, key: "p" }, { label: "区间", key: "r" }], (fc.predictions || []).map(p => ({ h: p.horizon_minutes + " 分钟", p: fmt(p.predicted_flow, 2), r: `${fmt(p.lower_bound, 1)} ~ ${fmt(p.upper_bound, 1)}` })));
  }
  function healthMini(h) {
    return table([{ label: "设备", key: "d" }, { label: "评分", num: true, key: "s" }, { label: "风险", key: "r" }, { label: "建议", key: "g" }], (h.devices || []).map(d => ({ d: d.device_id, s: d.health_score, r: riskBadge(d.risk_level), g: d.suggestion })));
  }

  /* ============================ 方案确认与执行日志（并入智能调度方案） ============================ */
  async function renderControl() {
    $("#ct-hint").textContent = "当前 as_of：" + (state.currentAsOf || "—") + "（可在上方“调度求解”选择时刻；点击各模式卡片上的确认/拒绝/回退记录日志）";
    const data = cache["clog"] || (cache["clog"] = await api("/api/control/log"));
    const log = data.log || [];
    const CN = { confirm: ["已确认", "ok"], reject: ["已拒绝", "err"], fallback: ["回退 B1", "fb"] };
    $("#ct-log").innerHTML = log.length ? table([{ label: "序号", num: true, key: "seq" }, { label: "时间", key: "ts" }, { label: "模式", key: "m" }, { label: "as_of", key: "a" },
      { label: "动作", key: "act", value: r => `<span class="pill ${CN[r.action] ? CN[r.action][1] : ""}">${CN[r.action] ? CN[r.action][0] : r.action}</span>` },
      { label: "权限", key: "perm" }], log.map(x => ({ ...x, m: MODE_CN[x.mode] || x.mode, a: x.as_of, perm: x.permission }))) : '<div class="muted">暂无日志，点击各模式卡片上的“确认 / 拒绝 / 回退”即可记录。</div>';
  }

  /* ============================ 演示：模拟播放 ============================ */
  function updateProgress(i, total) {
    $("#pb-bar").style.width = (total ? ((i + 1) / total) * 100 : 0) + "%";
    $("#pb-count").textContent = (i + 1) + " / " + total;
  }
  function bindPlaybackControls() {
    $("#pb-play").addEventListener("click", togglePlay);
    $("#pb-step").addEventListener("click", () => stepFrame(Math.min(state.idx + 1, state.times.length - 1)));
    $("#pb-reset").addEventListener("click", () => { stopPlay(); stepFrame(0); });
    $("#pb-speed").addEventListener("change", () => { state.speed = parseInt($("#pb-speed").value, 10); if (state.playing) { stopPlay(); startPlay(); } });
  }
  async function initPlayback() {
    bindPlaybackControls();
    try {
      const t = await api("/api/dispatch/times");
      state.times = t.times || [];
      state.idx = state.times.length ? state.times.length - 1 : 0;
      updateProgress(state.idx, state.times.length);
      await stepFrame(state.idx, false);
    } catch (e) {
      $("#pb-clock").textContent = "回放数据不可用";
      console.error("initPlayback", e);
    }
  }
  function togglePlay() { state.playing ? stopPlay() : startPlay(); }
  function startPlay() {
    if (!state.times.length) { alert("回放时间轴不可用"); return; }
    state.playing = true;
    $("#pb-play").textContent = "⏸ 暂停";
    $("#pb-status").style.display = "";
    state.timer = setInterval(() => {
      const nxt = state.idx + 1 >= state.times.length ? 0 : state.idx + 1;
      stepFrame(nxt);
    }, state.speed);
  }
  function stopPlay() {
    state.playing = false;
    $("#pb-play").textContent = "▶ 播放";
    $("#pb-status").style.display = "none";
    clearInterval(state.timer);
  }
  async function stepFrame(i) {
    if (!state.times.length) return;
    state.idx = i;
    try {
      const frame = cache["frame:" + i] || (cache["frame:" + i] = await api("/api/rt/frame?i=" + i));
      state.currentAsOf = frame.as_of;
      $("#pb-clock").textContent = frame.as_of;
      updateProgress(i, frame.total);
      // 无论当前在哪个页面都刷新机组实时状态，进入该页即见最新帧
      renderUnitsFrame(frame);
    } catch (e) {
      console.error("stepFrame", e);
    }
  }

  /* ============================ 演示：一键场景 ============================ */
  async function initDemo() {
    const sim = cache["sim"] || (cache["sim"] = await api("/api/simulation"));
    const scen = Object.keys((sim.summary || {}).scenarios || {});
    $("#demo-scenario").innerHTML = scen.map(s => `<option value="${s}">${s}</option>`).join("");
    $("#demo-run").addEventListener("click", () => runDemo($("#demo-scenario").value));
    $("#demo-close").addEventListener("click", () => $("#demo-modal").classList.add("hidden"));
  }
  async function runDemo(name) {
    $("#loading").classList.remove("hidden");
    try {
      const d = await api("/api/scenario/" + name);
      const c = d.comparison || {};
      $("#demo-title").textContent = "典型场景演示 · " + d.label;
      const labels = ((d.series.b1 || {}).time || []).map(t => String(t).slice(11, 16));
      $("#demo-body").innerHTML = `
        <div class="hint">${esc(d.note || "")}（工程假设闭环仿真；人工=B1 规则基线，智能=B3 能效优先）</div>
        <div class="grid-2" style="margin:10px 0">
          <div class="cmp-card"><div class="c-h">能耗 kWh</div><div class="c-v">${fmt(c.energy_kwh_b1, 0)} → <span class="good">${fmt(c.energy_kwh_b3, 0)}</span></div></div>
          <div class="cmp-card"><div class="c-h">成本</div><div class="c-v">${money(c.cost_b1)} → <span class="good">${money(c.cost_b3)}</span></div></div>
          <div class="cmp-card"><div class="c-h">压力合格率</div><div class="c-v">${pct((c.pressure_compliance_b1 || 0) * 100, 0)} → <span class="good">${pct((c.pressure_compliance_b3 || 0) * 100, 0)}</span></div></div>
          <div class="cmp-card"><div class="c-h">压力波动降幅</div><div class="c-v good">${pct(c.pressure_std_reduction_pct, 1)}</div></div>
        </div>
        <div class="card-title">母管压力：人工 vs 智能</div><div class="chart" id="demo-pressure"></div>
        <div class="card-title" style="margin-top:12px">供需</div><div class="chart" id="demo-flow"></div>`;
      Charts.line($("#demo-pressure"), { labels, height: 300, yMin: 0.5, yMax: 0.7, series: [
        { name: "人工基线", color: "#E5484D", data: (d.series.b1 || {}).pressure || [] },
        { name: "智能调度", color: "#2F6BFF", data: (d.series.b3 || {}).pressure || [] }] });
      Charts.line($("#demo-flow"), { labels, height: 300, series: [
        { name: "需求", color: "#E5484D", data: (d.series.b1 || {}).demand || [] },
        { name: "人工供气", color: "#9AA7B8", data: (d.series.b1 || {}).supply || [] },
        { name: "智能供气", color: "#00B3A4", data: (d.series.b3 || {}).supply || [] }] });
      $("#demo-modal").classList.remove("hidden");
    } catch (e) { alert("场景演示失败：" + e.message); }
    finally { $("#loading").classList.add("hidden"); }
  }

  /* ============================ 11 测试验收 ============================ */
  function evidenceBadge(label) {
    const t = String(label || "");
    const cls = t.includes("无法") ? "risk-unknown" : (t.includes("仿真") ? "risk-medium" : "risk-low");
    const name = t.includes("无法") ? "无法验证" : (t.includes("仿真") ? "仿真验证" : "实测");
    return `<span class="pill ${cls}">${name}</span>`;
  }
  async function renderAcceptance() {
    const d = cache["acceptance"] || (cache["acceptance"] = await api("/api/acceptance"));
    if (!d || d.available === false || !d.summary) {
      $("#ac-summary").innerHTML = '<div class="muted">暂无验收结果，请先运行 analysis/scripts/webapp_acceptance.py。</div>';
      return;
    }
    $("#ac-summary").innerHTML = table(
      [{ label: "#", num: true, key: "no" }, { label: "指标", key: "metric" }, { label: "结果", key: "value" },
       { label: "单位", key: "unit" }, { label: "证据分级", key: "v", value: r => evidenceBadge(r.evidence_label) },
       { label: "结论", key: "conclusion" }],
      d.summary.map(r => ({ ...r, v: "" })));

    const f = d.forecast.metrics;
    $("#ac-forecast").innerHTML = table([{ label: "时域", key: "h" }, { label: "n", num: true, key: "n" }, { label: "MAPE", num: true, key: "mape" },
      { label: "RMSE", num: true, key: "rmse" }, { label: "MAE", num: true, key: "mae" }, { label: "WAPE", num: true, key: "wape" }, { label: "90%覆盖", num: true, key: "cov" }],
      Object.entries(f).map(([h, v]) => ({ h, n: v.n, mape: pct(v.MAPE, 2), rmse: fmt(v.RMSE, 2), mae: fmt(v.MAE, 2), wape: pct(v.WAPE, 2), cov: v.coverage_90 })));

    const lat = d.dispatch_latency.by_mode;
    $("#ac-latency").innerHTML = table([{ label: "模式", key: "m" }, { label: "n", num: true, key: "n" }, { label: "p50(ms)", num: true, key: "p50" },
      { label: "p95(ms)", num: true, key: "p95" }, { label: "max(ms)", num: true, key: "max" }, { label: "成功/回退", key: "sf" }, { label: "达标", key: "ok" }],
      Object.entries(lat).map(([m, v]) => ({ m: MODE_CN[m] || m, n: v.n, p50: v.p50_ms, p95: v.p95_ms, max: v.max_ms, sf: `${v.success} / ${v.fallback}`, ok: v.meet_target ? '<span class="pill risk-low">是</span>' : '<span class="pill risk-high">否</span>' })));

    const sim = d.pressure.simulation.scenarios;
    $("#ac-pressure").innerHTML = `<div class="hint">历史实测合格率 ${d.pressure.historical.within_reference_band_share}（压力带为假设）</div>` +
      table([{ label: "场景", key: "s" }, { label: "合格率B1", num: true, key: "c1" }, { label: "合格率B3", num: true, key: "c3" },
        { label: "std降幅", num: true, key: "r" }],
        Object.entries(sim).map(([s, v]) => ({ s, c1: v.compliance_b1, c3: v.compliance_b3, r: v.compliance_b1 != null ? pct(v.std_reduction_pct, 1) : "—" })));

    const en = d.energy.segments || {};
    $("#ac-energy").innerHTML = `<div class="hint">口径 ${d.energy.scope}；电价 ${d.energy.price_cny_per_kwh} 元/kWh（假设）</div>` +
      table([{ label: "策略", key: "n" }, { label: "SEC", num: true, key: "s" }, { label: "能效提升", num: true, key: "sr" }, { label: "成本降低", num: true, key: "cr" }],
        Object.values(en).map(v => ({ n: v.name, s: fmt(v.SEC, 4), sr: pct(v.sec_reduction_pct, 2), cr: pct(v.cost_reduction_pct, 2) })));

    const ag = d.agent_intent;
    $("#ac-agent").innerHTML = `<div class="hint">用例 ${ag.n}，正确 ${ag.correct}，准确率 <b>${pct(ag.accuracy * 100, 1)}</b></div>` +
      table([{ label: "输入", key: "text" }, { label: "期望", key: "expect" }, { label: "实际", key: "got" }, { label: "命中", key: "hit", value: r => r.hit ? '<span class="pill risk-low">✓</span>' : '<span class="pill risk-high">✗</span>' }], ag.cases);

    $("#ac-api").innerHTML = `<div class="hint">可用性 ${pct(d.api.availability * 100, 1)}（已预热稳态）</div>` +
      table([{ label: "端点", key: "endpoint" }, { label: "p50(ms)", num: true, key: "p50" }, { label: "p95(ms)", num: true, key: "p95" }, { label: "max(ms)", num: true, key: "max" }, { label: "成功", key: "ok" }],
        d.api.endpoints.map(e => ({ endpoint: e.endpoint, p50: e.p50_ms, p95: e.p95_ms, max: e.max_ms, ok: `${e.ok}/${e.n}` })));
  }

  /* ============================ 12 知识中心 ============================ */
  const ANS_TYPE = { realtime: ["实时分析", "risk-attention"], knowledge: ["知识库回答", "risk-low"],
                     combined: ["综合决策", "risk-medium"], safety: ["安全提示", "risk-high"] };
  const EV_CLASS = { "真实数据": "ev-real", "离线回放": "ev-replay", "仿真": "ev-sim",
                     "混合仿真": "ev-mixed", "项目演示规则": "ev-rule" };
  function answerTypeBadge(t) {
    const m = ANS_TYPE[t] || [t || "结果", "risk-unknown"];
    return `<span class="pill ${m[1]}">${m[0]}</span>`;
  }
  function evidenceTag(mode) {
    if (!mode) return "";
    const cls = EV_CLASS[mode] || "ev-rule";
    return `<span class="ev ${cls}">验证方式：${esc(mode)}</span>`;
  }
  function citationList(citations) {
    if (!citations || !citations.length) return "";
    const seen = new Set();
    const items = citations.filter(c => { const k = c.knowledge_id || c.title; if (seen.has(k)) return false; seen.add(k); return true; })
      .map((c, i) => `<div class="cite-item">
        <div class="cite-title">参考依据 ${i + 1} · ${esc(c.title || "")}</div>
        <div class="cite-meta">分类：${esc(c.category || "—")} ｜ 章节：${esc(c.section || "—")}</div>
        <div class="cite-meta">来源：<span class="mono">${esc(c.source_path || "—")}</span></div>
        <div class="cite-meta">相关度：${c.score == null ? "—" : fmt(c.score * 100, 1) + "%"}</div>
      </div>`).join("");
    return `<details class="cites"><summary>参考依据（${citations.length}）</summary>${items}</details>`;
  }
  function knowledgeBadge(st) {
    const s = (st || {}).status;
    if (s === "ready") return '<span class="pill risk-low">正常</span>';
    if (s === "stale") return '<span class="pill risk-medium">索引待重建</span>';
    return '<span class="pill risk-high">不可用</span>';
  }
  function knowledgeCard(st) {
    const stale = st.stale || st.status === "stale";
    const msg = st.status === "stale" ? "知识来源已更新，索引等待重建"
      : (st.status === "unavailable" ? (st.message || "服务不可用") : "");
    return `<div class="kb-card">
      <div class="kb-card-head">知识库 ${knowledgeBadge(st)}</div>
      <div class="kb-card-row"><span>知识文档</span><b>${st.document_count ?? "—"}</b></div>
      <div class="kb-card-row"><span>知识块</span><b>${st.chunk_count ?? "—"}</b></div>
      <div class="kb-card-row"><span>版本</span><b>${esc(st.kb_version || "—")}</b></div>
      <div class="kb-card-row"><span>更新时间</span><b>${esc(st.built_at || "—")}</b></div>
      ${msg ? `<div class="hint">${esc(msg)}</div>` : ""}
      ${stale ? "" : '<button class="btn btn-sm" id="ov-kb-go">进入知识中心</button>'}
    </div>`;
  }

  let kbBound = false;
  async function renderKnowledge() {
    if (!kbBound) {
      kbBound = true;
      $("#kb-ask").addEventListener("click", () => kbAsk($("#kb-input").value));
      $("#kb-clear").addEventListener("click", () => { $("#kb-input").value = ""; renderKbEmpty(); });
      $("#kb-input").addEventListener("keydown", e => { if (e.key === "Enter") kbAsk($("#kb-input").value); });
    }
    try {
      const st = await api("/api/knowledge/status");
      renderKbStatus(st);
      const cats = await api("/api/knowledge/categories");
      $("#kb-cats").innerHTML = (cats.categories || []).map(c => `<span class="kb-cat">${esc(c)}</span>`).join("");
      cache.kbRecommended = cats.recommended || [];
    } catch (e) {
      renderKbStatus({ status: "unavailable", message: e.message });
      cache.kbRecommended = cache.kbRecommended || [];
    }
    if (!$("#kb-answer").dataset.done) renderKbEmpty();
  }
  function renderKbStatus(st) {
    $("#kb-status-badge").innerHTML = knowledgeBadge(st);
    const stale = st.stale || st.status === "stale";
    const msg = st.status === "unavailable" ? (st.message || "知识库服务不可用")
      : (stale ? "知识来源已更新，索引等待重建" : "服务正常");
    $("#kb-statusbar").innerHTML =
      `<span class="kb-st"><b>状态</b> ${knowledgeBadge(st)}</span>
       <span class="kb-st"><b>文档数</b> ${st.document_count ?? "—"}</span>
       <span class="kb-st"><b>知识块</b> ${st.chunk_count ?? "—"}</span>
       <span class="kb-st"><b>版本</b> ${esc(st.kb_version || "—")}</span>
       <span class="kb-st"><b>更新时间</b> ${esc(st.built_at || "—")}</span>
       <span class="kb-st muted">${esc(msg)}</span>`;
  }
  function renderKbEmpty() {
    $("#kb-answer").innerHTML = `<div class="muted">输入问题或选择推荐问题开始查询。</div>
      <div class="quick" id="kb-reco">${(cache.kbRecommended || []).map(q => `<button data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>`;
    const r = $("#kb-reco");
    if (r) r.addEventListener("click", e => { const b = e.target.closest("button"); if (b) { $("#kb-input").value = b.dataset.q; kbAsk(b.dataset.q); } });
    $("#kb-answer").dataset.done = "1";
  }
  async function kbAsk(question) {
    const q = (question || "").trim();
    if (!q) return;
    $("#kb-input").value = q;
    const btn = $("#kb-ask"); btn.disabled = true; btn.textContent = "查询中…";
    $("#kb-answer").innerHTML = '<div class="muted">检索中…</div>';
    delete $("#kb-answer").dataset.done;
    try {
      const d = await api("/api/knowledge/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: q, top_k: 5 }) });
      renderKbAnswer(d);
    } catch (e) {
      $("#kb-answer").innerHTML = `<div class="warn-banner">查询失败：${esc(e.message)}（可稍后重试，问题已保留）</div>`;
    } finally {
      btn.disabled = false; btn.textContent = "提问";
    }
  }
  function renderKbAnswer(d) {
    const ok = d.status === "success";
    const head = `<div class="ans-head">${ok ? '<span class="pill risk-low">知识库回答</span>' : '<span class="pill risk-medium">暂无可靠依据</span>'}
      ${ok ? evidenceTag(d.validation_mode) : ""}</div>`;
    const meta = ok ? `<div class="hint">检索相关度 ${fmt((d.confidence || 0) * 100, 0)}% ｜ 查询耗时 ${fmt((d.elapsed_seconds || 0), 3)} s ｜ 版本 ${esc(d.kb_version || "—")}</div>` : "";
    const body = `<div class="ans-body">${esc(d.answer || "")}</div>`;
    const cites = ok ? citationList(d.citations) : "";
    const tail = ok ? "" : '<div class="hint">知识库没有匹配到可靠来源，可尝试换一种问法或补充知识文档。</div>';
    $("#kb-answer").innerHTML = head + body + meta + cites + tail;
  }

  /* ============================ 路由 ============================ */
  const RENDER = { overview: renderOverview, units: renderUnits, forecast: renderForecast, dispatch: renderDispatch, health: renderHealth, surge: renderSurge, energy: renderEnergy, alarms: renderAlarms, agent: renderAgent, knowledge: renderKnowledge, acceptance: renderAcceptance };
  function activate(target) {
    $$(".nav-item").forEach(n => n.classList.toggle("active", n.dataset.target === target));
    $$(".panel").forEach(p => p.classList.toggle("active", p.id === target));
    const meta = PAGE_META[target] || ["", ""];
    $("#page-title").textContent = meta[0]; $("#page-sub").textContent = meta[1];
    const fn = RENDER[target];
    if (fn) fn().catch(e => console.error(target, e));
    if (target === "dispatch") renderPressureCompare().catch(() => {});
  }
  $("#nav").addEventListener("click", e => { const item = e.target.closest(".nav-item"); if (item) activate(item.dataset.target); });

  $("#loading").classList.remove("hidden");
  (async () => {
    try { await initPlayback(); } catch (e) { console.error(e); }
    try { await renderOverview(); } catch (e) { console.error(e); }
    try { await initDemo(); } catch (e) { console.error(e); }
    try { await renderDispatch(); } catch (e) { console.error(e); }
  })().finally(() => $("#loading").classList.add("hidden"));
})();
