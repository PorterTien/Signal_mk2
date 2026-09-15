/* Signal — Dashboard JS */
"use strict";

// ---------------------------------------------------------------------------
// Ghost / demo data (shown when the DB has no real records yet)
// ---------------------------------------------------------------------------
const GHOST = {
  topApps: [
    { app_name: "Google Chrome",  minutes: 187, samples: 22 },
    { app_name: "Cursor",         minutes: 134, samples: 16 },
    { app_name: "Slack",          minutes:  91, samples: 11 },
    { app_name: "Terminal",       minutes:  63, samples:  8 },
    { app_name: "Notion",         minutes:  47, samples:  6 },
    { app_name: "Figma",          minutes:  38, samples:  5 },
    { app_name: "Mail",           minutes:  22, samples:  3 },
    { app_name: "Calendar",       minutes:  14, samples:  2 },
  ],
  heatmap: {
    "00":0,"01":0,"02":0,"03":0,"04":0,"05":0,
    "06":4,"07":12,"08":28,"09":47,"10":52,"11":49,
    "12":18,"13":41,"14":55,"15":61,"16":58,"17":43,
    "18":22,"19":14,"20":8,"21":3,"22":0,"23":0,
  },
  trend: [
    { day: null, hours: 5.2 },
    { day: null, hours: 6.8 },
    { day: null, hours: 4.1 },
    { day: null, hours: 7.3 },
    { day: null, hours: 6.5 },
    { day: null, hours: 3.9 },
    { day: null, hours: 7.1 },
  ],
  stats: { hours_today: 7.1, events_today: 5, db_days: 0 },
};

// Fill trend days relative to today
(function () {
  for (let i = 0; i < 7; i++) {
    const d = new Date();
    d.setDate(d.getDate() - (6 - i));
    GHOST.trend[i].day = d.toISOString().slice(0, 10);
  }
})();

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
const state = {
  startDate: null,
  endDate: null,
  charts: {},
  usingGhost: false,
};

// ---------------------------------------------------------------------------
// Utility
// ---------------------------------------------------------------------------
const $  = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

function todayStr() {
  return new Date().toISOString().slice(0, 10);
}

function sevenDaysAgoStr() {
  const d = new Date();
  d.setDate(d.getDate() - 6);
  return d.toISOString().slice(0, 10);
}

async function apiFetch(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status} — ${url}`);
  return res.json();
}

function buildParams(extra = {}) {
  const p = new URLSearchParams();
  if (state.startDate) p.set("start", state.startDate + "T00:00:00");
  if (state.endDate)   p.set("end",   state.endDate   + "T23:59:59");
  Object.entries(extra).forEach(([k, v]) => p.set(k, v));
  return p.toString();
}

// ---------------------------------------------------------------------------
// Markdown-ish → HTML (for AI output)
// ---------------------------------------------------------------------------
function renderMarkdown(text) {
  return text
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    // Bold **…**
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    // Headers ## or ###
    .replace(/^#{1,3} (.+)$/gm, "<h4>$1</h4>")
    // Bullet points
    .replace(/^[•\-\*] (.+)$/gm, "<li>$1</li>")
    // Numbered items
    .replace(/^\d+\. (.+)$/gm, "<li>$1</li>")
    // Line breaks
    .replace(/\n\n+/g, "</p><p>")
    .replace(/\n/g, "<br>");
}

// ---------------------------------------------------------------------------
// Stats cards
// ---------------------------------------------------------------------------
function setGhostBadge(visible) {
  let badge = $("#ghost-badge");
  if (!badge) return;
  badge.style.display = visible ? "flex" : "none";
}

async function loadStats() {
  try {
    const data = await apiFetch("/api/stats-summary");
    const isEmpty = data.db_days === 0;
    state.usingGhost = isEmpty;
    setGhostBadge(isEmpty);
    const d = isEmpty ? GHOST.stats : data;
    $("#stat-hours").textContent  = d.hours_today + "h";
    $("#stat-events").textContent = d.events_today;
    $("#stat-days").textContent   = isEmpty ? "—" : d.db_days;
  } catch (e) {
    console.warn("stats-summary error:", e);
  }
}

// ---------------------------------------------------------------------------
// Top Apps bar chart
// ---------------------------------------------------------------------------
async function loadTopApps() {
  let raw = await apiFetch(`/api/top-apps?${buildParams({ limit: 15 })}`);
  const data = (raw.length === 0) ? GHOST.topApps : raw;

  const labels = data.map((d) => d.app_name);
  const values = data.map((d) => d.minutes);

  const ctx = $("#chart-top-apps").getContext("2d");

  if (state.charts.topApps) state.charts.topApps.destroy();
  state.charts.topApps = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [
        {
          label: "Minutes",
          data: values,
          backgroundColor: "rgba(99, 179, 237, 0.75)",
          borderColor: "rgba(99, 179, 237, 1)",
          borderWidth: 1,
          borderRadius: 4,
        },
      ],
    },
    options: {
      indexAxis: "y",
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: {
          grid: { color: "rgba(255,255,255,0.07)" },
          ticks: { color: "#a0aec0" },
        },
        y: {
          grid: { display: false },
          ticks: { color: "#e2e8f0", font: { size: 12 } },
        },
      },
      onClick(evt, elements) {
        if (!elements.length) return;
        const appName = labels[elements[0].index];
        loadDrilldown(appName);
      },
      onHover(evt, elements) {
        evt.native.target.style.cursor = elements.length ? "pointer" : "default";
      },
    },
  });
}

// ---------------------------------------------------------------------------
// Hourly heatmap (bar chart, 24 hours)
// ---------------------------------------------------------------------------
async function loadHeatmap() {
  const date = state.startDate || todayStr();
  const raw  = await apiFetch(`/api/hourly-heatmap?date=${date}`);
  const allZero = Object.values(raw).every((v) => v === 0);
  const data = allZero ? GHOST.heatmap : raw;

  const labels = Object.keys(data).map((h) => `${h}:00`);
  const values = Object.values(data);

  const ctx = $("#chart-heatmap").getContext("2d");
  const maxVal = Math.max(...values, 1);
  const colors = values.map((v) => {
    const alpha = 0.15 + 0.8 * (v / maxVal);
    return `rgba(154, 117, 255, ${alpha.toFixed(2)})`;
  });

  if (state.charts.heatmap) state.charts.heatmap.destroy();
  state.charts.heatmap = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [
        {
          label: "Minutes Active",
          data: values,
          backgroundColor: colors,
          borderColor: "rgba(154, 117, 255, 0.9)",
          borderWidth: 1,
          borderRadius: 3,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: {
          grid: { display: false },
          ticks: { color: "#718096", font: { size: 10 }, maxRotation: 45 },
        },
        y: {
          grid: { color: "rgba(255,255,255,0.07)" },
          ticks: { color: "#a0aec0" },
        },
      },
    },
  });
}

// ---------------------------------------------------------------------------
// 7-day trend line chart
// ---------------------------------------------------------------------------
async function loadTrend() {
  const raw = await apiFetch("/api/seven-day-trend");

  // Fill missing days with 0
  const days = {};
  for (let i = 6; i >= 0; i--) {
    const d = new Date();
    d.setDate(d.getDate() - i);
    days[d.toISOString().slice(0, 10)] = 0;
  }
  const srcData = (raw.length === 0) ? GHOST.trend : raw;
  srcData.forEach((row) => { days[row.day] = row.hours; });

  const labels = Object.keys(days).map((d) => {
    const dt = new Date(d + "T12:00:00");
    return dt.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
  });
  const values = Object.values(days);

  const ctx = $("#chart-trend").getContext("2d");
  if (state.charts.trend) state.charts.trend.destroy();
  state.charts.trend = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "Hours Tracked",
          data: values,
          borderColor: "rgba(72, 199, 142, 1)",
          backgroundColor: "rgba(72, 199, 142, 0.12)",
          fill: true,
          tension: 0.35,
          pointBackgroundColor: "rgba(72, 199, 142, 1)",
          pointRadius: 5,
          pointHoverRadius: 7,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: {
          grid: { color: "rgba(255,255,255,0.07)" },
          ticks: { color: "#a0aec0" },
        },
        y: {
          beginAtZero: true,
          grid: { color: "rgba(255,255,255,0.07)" },
          ticks: { color: "#a0aec0" },
        },
      },
    },
  });
}

// ---------------------------------------------------------------------------
// App drilldown — window titles
// ---------------------------------------------------------------------------
async function loadDrilldown(appName) {
  const card  = $("#drilldown-card");
  const title = $("#drilldown-title");
  const list  = $("#drilldown-list");

  title.textContent = `"${appName}" — Window Titles`;
  list.innerHTML = '<div style="color:#718096;font-size:13px;padding:8px 0">Loading…</div>';
  card.style.display = "block";

  const params = buildParams({ app: appName, limit: 30 });
  let rows = await apiFetch(`/api/window-titles?${params}`);

  // Ghost data for Chrome when no real data
  if (rows.length === 0 && state.usingGhost && appName === "Google Chrome") {
    rows = [
      { window_title: "Claude - Anthropic",              minutes: 42, samples: 5 },
      { window_title: "GitHub — Signal_mk2",             minutes: 31, samples: 4 },
      { window_title: "localhost:5000 — Signal Dashboard", minutes: 24, samples: 3 },
      { window_title: "Notion — Tasks",                  minutes: 18, samples: 2 },
      { window_title: "Gmail",                           minutes: 14, samples: 2 },
      { window_title: "Google Search",                   minutes:  9, samples: 1 },
    ];
  }

  if (rows.length === 0) {
    list.innerHTML = '<div style="color:#718096;font-size:13px;padding:8px 0">No window title data yet — titles are captured after the next tracker poll.</div>';
    return;
  }

  const maxMins = rows[0].minutes;
  list.innerHTML = rows.map((r) => {
    const urlHtml = r.url
      ? `<div class="drilldown-url"><a href="${r.url}" target="_blank" rel="noopener">${r.url}</a></div>`
      : "";
    return `
    <div class="drilldown-row">
      <div class="drilldown-meta">
        <div class="drilldown-label" title="${r.window_title}">${r.window_title}</div>
        ${urlHtml}
      </div>
      <div class="drilldown-bar-wrap">
        <div class="drilldown-bar" style="width:${Math.round(100 * r.minutes / maxMins)}%"></div>
      </div>
      <div class="drilldown-mins">${r.minutes}m</div>
    </div>`;
  }).join("");
}

// ---------------------------------------------------------------------------
// AI Bottleneck Analysis
// ---------------------------------------------------------------------------
async function runAnalysis() {
  const btn   = $("#analyze-btn");
  const panel = $("#ai-panel");
  const output = $("#ai-output");
  const loader = $("#ai-loader");

  btn.disabled = true;
  btn.textContent = "Analyzing…";
  panel.style.display = "block";
  output.innerHTML = "";
  loader.style.display = "flex";

  // Collect data
  let topApps = [], calendarEvents = [];
  try {
    topApps        = await apiFetch(`/api/top-apps?${buildParams({ limit: 15 })}`);
    calendarEvents = await apiFetch(`/api/calendar-events?${buildParams()}`);
  } catch (e) {
    output.innerHTML = `<span class="error">Failed to fetch data: ${e.message}</span>`;
    loader.style.display = "none";
    btn.disabled = false;
    btn.textContent = "Analyze Bottlenecks";
    return;
  }

  const dateRange = (state.startDate && state.endDate)
    ? `${state.startDate} to ${state.endDate}`
    : state.startDate
    ? `from ${state.startDate}`
    : "the last 7 days";

  try {
    const res = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ top_apps: topApps, calendar_events: calendarEvents, date_range: dateRange }),
    });

    if (!res.ok) {
      const err = await res.json();
      output.innerHTML = `<span class="error">${err.error || "API error"}</span>`;
      loader.style.display = "none";
      btn.disabled = false;
      btn.textContent = "Analyze Bottlenecks";
      return;
    }

    loader.style.display = "none";

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let fullText = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop(); // keep incomplete line

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const payload = line.slice(6).trim();
        if (payload === "[DONE]") break;
        try {
          const obj = JSON.parse(payload);
          if (obj.error) {
            output.innerHTML = `<span class="error">${obj.error}</span>`;
            break;
          }
          fullText += obj.text || "";
          output.innerHTML = "<p>" + renderMarkdown(fullText) + "</p>";
          output.scrollTop = output.scrollHeight;
        } catch (_) {}
      }
    }
  } catch (e) {
    output.innerHTML = `<span class="error">Stream error: ${e.message}</span>`;
    loader.style.display = "none";
  }

  btn.disabled = false;
  btn.textContent = "Analyze Bottlenecks";
}

// ---------------------------------------------------------------------------
// Date range picker
// ---------------------------------------------------------------------------
function initDatePicker() {
  const today = todayStr();
  const weekAgo = sevenDaysAgoStr();
  state.startDate = weekAgo;
  state.endDate   = today;

  flatpickr("#date-range", {
    mode: "range",
    dateFormat: "Y-m-d",
    defaultDate: [weekAgo, today],
    theme: "dark",
    onChange(dates) {
      if (dates.length === 2) {
        state.startDate = dates[0].toISOString().slice(0, 10);
        state.endDate   = dates[1].toISOString().slice(0, 10);
        refreshCharts();
      }
    },
  });
}

// ---------------------------------------------------------------------------
// Refresh
// ---------------------------------------------------------------------------
async function refreshCharts() {
  await Promise.all([loadTopApps(), loadHeatmap(), loadTrend()]);
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
  initDatePicker();
  loadStats();
  refreshCharts();

  $("#analyze-btn").addEventListener("click", runAnalysis);

  // Refresh stats every 30 s while page is open
  setInterval(loadStats, 30_000);
});
