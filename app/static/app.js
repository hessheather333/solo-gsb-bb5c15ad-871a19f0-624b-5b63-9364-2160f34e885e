/* URL 重写规则调试台 —— 原生 JavaScript，无任何依赖或构建步骤。 */
"use strict";

const $ = (sel) => document.querySelector(sel);

const els = {
  exampleSelect: $("#exampleSelect"),
  editorWrap: $("#editorWrap"),
  gutter: $("#gutter"),
  rules: $("#rulesInput"),
  errorBox: $("#errorBox"),
  path: $("#pathInput"),
  maxSteps: $("#maxStepsInput"),
  runBtn: $("#runBtn"),
  emptyState: $("#emptyState"),
  resultBody: $("#resultBody"),
  resultActions: $("#resultActions"),
  summary: $("#summary"),
  initialUrl: $("#initialUrl"),
  finalUrl: $("#finalUrl"),
  timeline: $("#timeline"),
  copyTextBtn: $("#copyTextBtn"),
  copyJsonBtn: $("#copyJsonBtn"),
  toast: $("#toast"),
};

let lastReport = null; // 最近一次成功调试的完整结果，用于复制

// ---------- 工具 ----------

function escapeHtml(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (ch) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      })[ch]
  );
}

function toast(message, isError = false) {
  els.toast.textContent = message;
  els.toast.classList.toggle("error", isError);
  els.toast.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => {
    els.toast.hidden = true;
  }, 2200);
}

async function copyToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (_) {
    // 非安全上下文（http + 非 localhost）时的兜底方案
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
}

// ---------- 行号槽 ----------

function renderGutter(markers = {}) {
  const lineCount = els.rules.value.split("\n").length || 1;
  let html = "";
  for (let i = 1; i <= lineCount; i++) {
    const cls = markers[i] ? `gutter-line ${markers[i]}` : "gutter-line";
    html += `<span class="${cls}">${i}</span>`;
  }
  els.gutter.innerHTML = html;
}

els.rules.addEventListener("input", () => renderGutter());
els.rules.addEventListener("scroll", () => {
  els.gutter.scrollTop = els.rules.scrollTop;
});

function focusRuleLine(lineNo) {
  const lines = els.rules.value.split("\n");
  const start = lines.slice(0, lineNo - 1).reduce((n, l) => n + l.length + 1, 0);
  const end = start + (lines[lineNo - 1]?.length ?? 0);
  els.rules.focus();
  els.rules.setSelectionRange(start, end);
  // 将目标行滚动到文本框约 1/3 处
  const lineHeight = parseFloat(getComputedStyle(els.rules).lineHeight) || 20;
  els.rules.scrollTop = Math.max(0, (lineNo - 3) * lineHeight);
}

// ---------- 示例载入 ----------

async function loadExamples() {
  try {
    const examples = await fetch("/api/examples").then((r) => r.json());
    for (const ex of examples) {
      const opt = document.createElement("option");
      opt.value = ex.id;
      opt.textContent = ex.name;
      els.exampleSelect.appendChild(opt);
    }
    els.exampleSelect._data = examples;
  } catch (err) {
    console.error("加载示例失败", err);
  }
}

els.exampleSelect.addEventListener("change", () => {
  const ex = (els.exampleSelect._data || []).find(
    (e) => e.id === els.exampleSelect.value
  );
  if (!ex) return;
  els.rules.value = ex.rules;
  els.path.value = ex.path;
  els.maxSteps.value = ex.max_steps ?? 50;
  renderGutter();
  clearErrors();
  toast(`已载入示例：${ex.name.replace(/^\d+\.\s*/, "")}`);
});

// ---------- 语法错误 ----------

function clearErrors() {
  els.errorBox.hidden = true;
  els.errorBox.innerHTML = "";
  renderGutter();
}

function showErrors(payload) {
  const markers = {};
  let html = `<h4>✗ ${escapeHtml(payload.message)}</h4>`;
  for (const err of payload.errors || []) {
    markers[err.line] = "error";
    html += `
      <div class="error-item" data-line="${err.line}">
        <span class="error-loc">第 ${err.line} 行:${err.column} 列</span>
        <span>
          ${escapeHtml(err.message)}
          ${err.snippet ? `<span class="snippet">${escapeHtml(err.snippet)}</span>` : ""}
        </span>
      </div>`;
  }
  els.errorBox.innerHTML = html;
  els.errorBox.hidden = false;
  renderGutter(markers);
  els.errorBox.querySelectorAll(".error-item").forEach((node) => {
    node.addEventListener("click", () => focusRuleLine(Number(node.dataset.line)));
  });
}

// ---------- 结果渲染 ----------

const TERM_META = {
  exhausted: { label: "规则链执行完毕", cls: "ok" },
  break: { label: "break 内部改写", cls: "ok" },
  redirect: { label: "重定向终止", cls: "redirect" },
  loop: { label: "检测到循环 (508)", cls: "loop" },
  step_limit: { label: "超过步数上限 (508)", cls: "loop" },
  error: { label: "执行错误", cls: "loop" },
};

function highlightMatch(url, span) {
  if (!span) return escapeHtml(url);
  const [s, e] = span;
  return (
    escapeHtml(url.slice(0, s)) +
    `<span class="match-hl">${escapeHtml(url.slice(s, e))}</span>` +
    escapeHtml(url.slice(e))
  );
}

function renderGroups(groups) {
  const entries = Object.entries(groups || {});
  if (!entries.length) return '<span class="step-note">（无捕获组）</span>';
  return entries
    .map(([key, val]) => {
      const isNum = /^\d+$/.test(key);
      const v =
        val === null || val === undefined
          ? '<span class="null">未参与匹配 (None)</span>'
          : escapeHtml(val);
      return `<span class="group-chip"><b>${isNum ? "$" + key : "${" + key + "}"}</b>=${v}</span>`;
    })
    .join(" ");
}

function renderReport(rep) {
  lastReport = rep;
  els.emptyState.hidden = true;
  els.resultBody.hidden = false;
  els.resultActions.hidden = false;

  // 概要徽章
  const meta = TERM_META[rep.terminated_by] || TERM_META.exhausted;
  const statusText = rep.status_text ? ` ${rep.status_text}` : "";
  els.summary.innerHTML = `
    <span class="badge ${meta.cls}">HTTP ${rep.status_code}${escapeHtml(statusText)}</span>
    <span class="badge info">${escapeHtml(meta.label)}</span>
    <span class="badge info">评估 ${rep.evaluations} 次</span>
    <span class="badge info">规则轮次 ${rep.passes}</span>
    <span class="badge info">上限 ${rep.max_steps} 步</span>
    <p class="stop-detail">${escapeHtml(rep.stop_detail)}</p>`;

  els.initialUrl.textContent = rep.initial_url;
  els.finalUrl.textContent = rep.final_url;

  // 时间线
  const hitLines = new Set();
  const cards = rep.steps.map((step, idx) => {
    if (step.matched) hitLines.add(step.rule_line);
    const flags = step.flags ? ` <span class="rule-line">[${escapeHtml(step.flags)}]</span>` : "";
    const pill = step.matched
      ? `<span class="pill ${escapeHtml(step.action || "")}">${
          step.action === "redirect"
            ? "REDIRECT"
            : (step.action || "").toUpperCase()
        }</span>`
      : '<span class="pill miss">未命中</span>';

    const body = step.matched
      ? `
        <div class="step-line"><span class="k">输入</span><code>${highlightMatch(
          step.input_url,
          step.match_span
        )}</code></div>
        <div class="step-line"><span class="k">匹配</span><code>${escapeHtml(
          step.matched_text
        )} <span class="step-note">@ [${step.match_span.join(", ")}]</span></code></div>
        <div class="step-line"><span class="k">变量</span><div style="display:flex;gap:6px;flex-wrap:wrap">${renderGroups(
          step.groups
        )}</div></div>
        <div class="step-line"><span class="k">输出</span><code class="${
          step.changed ? "changed" : ""
        }">${escapeHtml(step.output_url)}</code>${
          step.changed ? '<span class="step-note">URL 已变化</span>' : '<span class="step-note">URL 未变化</span>'
        }</div>
        <div class="step-note">↳ ${escapeHtml(step.note)}</div>`
      : `
        <div class="step-line"><span class="k">输入</span><code>${escapeHtml(
          step.input_url
        )}</code></div>
        <div class="step-note">↳ ${escapeHtml(step.note)}</div>`;

    return `
      <li class="step-card ${step.matched ? "hit" : "miss"}" data-line="${step.rule_line}">
        <div class="step-head">
          <span class="step-no">#${idx + 1}</span>
          <span class="pill pass">第 ${step.pass} 轮</span>
          <span class="step-rule"><span class="rule-line">L${step.rule_line}:</span>
            ${escapeHtml(step.pattern)} → ${escapeHtml(step.replacement)}${flags}
          </span>
          ${pill}
        </div>
        <div class="step-body">${body}</div>
      </li>`;
  });
  els.timeline.innerHTML = cards.join("");

  els.timeline.querySelectorAll(".step-card").forEach((card) => {
    card.addEventListener("click", () => focusRuleLine(Number(card.dataset.line)));
  });

  // 在行号槽上同时标出错误（若有）与命中行
  const markers = {};
  for (const line of hitLines) markers[line] = "active";
  renderGutter(markers);
}

// ---------- 文本报告（用于复制） ----------

function buildTextReport(rep) {
  const lines = [];
  lines.push("URL 重写调试报告");
  lines.push("================");
  lines.push(`输入路径 : ${rep.initial_url}`);
  lines.push(`最终 URL : ${rep.final_url}`);
  lines.push(`响应状态 : HTTP ${rep.status_code} ${rep.status_text}`);
  lines.push(`终止原因 : ${rep.stop_detail}`);
  lines.push(
    `评估次数 : ${rep.evaluations}（规则轮次 ${rep.passes}，步数上限 ${rep.max_steps}）`
  );
  lines.push("");
  lines.push("逐步执行:");

  rep.steps.forEach((step, idx) => {
    const flags = step.flags ? ` [${step.flags}]` : "";
    lines.push(
      `[${String(idx + 1).padStart(2, " ")}] 第${step.pass}轮 行L${step.rule_line}  ` +
        `${step.pattern} -> ${step.replacement}${flags}  ` +
        (step.matched ? `✔ 命中 (${step.action || "-"})` : "✘ 未命中")
    );
    if (step.matched) {
      lines.push(`     输入 : ${step.input_url}`);
      lines.push(
        `     匹配 : ${step.matched_text} @ [${step.match_span.join(", ")}]`
      );
      const groups = Object.entries(step.groups || {})
        .map(([k, v]) => `${/^\d+$/.test(k) ? "$" + k : "${" + k + "}"}=${v ?? "None"}`)
        .join("  ");
      if (groups) lines.push(`     变量 : ${groups}`);
      lines.push(
        `     输出 : ${step.output_url}${step.changed ? "  (URL 已变化)" : ""}`
      );
    } else {
      lines.push(`     输入 : ${step.input_url}`);
    }
    lines.push(`     说明 : ${step.note}`);
  });
  return lines.join("\n");
}

// ---------- 执行调试 ----------

async function runDebug() {
  const path = els.path.value.trim();
  if (!path) {
    toast("请输入请求路径", true);
    els.path.focus();
    return;
  }

  els.runBtn.disabled = true;
  els.runBtn.textContent = "模拟中…";
  try {
    const resp = await fetch("/api/debug", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        path,
        rules: els.rules.value,
        max_steps: Number(els.maxSteps.value) || 50,
      }),
    });

    const payload = await resp.json();
    if (resp.status === 422 && payload.error === "rule_syntax") {
      lastReport = null;
      els.resultActions.hidden = true;
      els.resultBody.hidden = true;
      els.emptyState.hidden = false;
      showErrors(payload);
      toast(`发现 ${payload.errors.length} 处规则语法错误`, true);
      return;
    }
    if (!resp.ok) {
      const detail =
        typeof payload.detail === "string"
          ? payload.detail
          : JSON.stringify(payload.detail);
      toast(`请求失败：${detail}`, true);
      return;
    }

    clearErrors();
    renderReport(payload);
    toast("模拟完成");
  } catch (err) {
    console.error(err);
    toast("网络错误，无法连接调试服务", true);
  } finally {
    els.runBtn.disabled = false;
    els.runBtn.textContent = "▶ 逐步模拟";
  }
}

els.runBtn.addEventListener("click", runDebug);
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
    e.preventDefault();
    runDebug();
  }
});

// ---------- 复制 ----------

els.copyTextBtn.addEventListener("click", async () => {
  if (!lastReport) return;
  await copyToClipboard(buildTextReport(lastReport));
  toast("文本报告已复制到剪贴板");
});

els.copyJsonBtn.addEventListener("click", async () => {
  if (!lastReport) return;
  await copyToClipboard(JSON.stringify(lastReport, null, 2));
  toast("JSON 结果已复制到剪贴板");
});

// ---------- 初始化 ----------

renderGutter();
loadExamples();
