const state = {
  status: "",
  search: "",
  page: 1,
};
const pageSize = 24;
let performance = {};

const $ = (selector) => document.querySelector(selector);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatNumber(value) {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("en-US").format(value);
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: "UTC",
    timeZoneName: "short",
  }).format(date);
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return "—";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days) return days + "d " + hours + "h";
  if (hours) return hours + "h " + minutes + "m";
  return minutes + "m";
}

function statusLabel(status) {
  const labels = {
    active: "Active",
    inactive: "Inactive",
    dismantled: "Dismantled",
    unknown: "Unknown",
  };
  return labels[status] || status || "Unknown";
}

function statusDurationLabel(status) {
  if (status === "inactive") return "Inactive for";
  if (status === "dismantled") return "Dismantled";
  return "Live for";
}

function statusDurationSeconds(pillar) {
  if (pillar.status === "dismantled") return null;
  return pillar.status_seconds ?? pillar.live_seconds;
}

function performanceAvailable(item) {
  const percentage = Number(item?.percentage);
  return Number.isFinite(percentage) && Number(item?.expected) > 0;
}

function formatPerformance(item) {
  return performanceAvailable(item)
    ? Number(item.percentage).toFixed(1) + "%"
    : "—";
}

function performanceBarClass(point) {
  if (!performanceAvailable(point)) return "no-data";
  const percentage = Number(point.percentage);
  if (percentage >= 95) return "high";
  if (percentage >= 80) return "medium";
  return "low";
}

function performanceBarHeight(point) {
  if (!performanceAvailable(point)) return 16;
  return Math.max(4, Math.min(100, Number(point.percentage)));
}

function renderPerformanceChart(item) {
  const points = Array.isArray(item?.daily) ? item.daily : [];
  if (!points.length) {
    return '<span class="performance-chart-empty">Daily data is not available yet.</span>';
  }
  return points.map((point) =>
    '<span class="performance-bar ' + performanceBarClass(point) +
    '" style="height:' + performanceBarHeight(point) + '%" aria-hidden="true"></span>'
  ).join("");
}

function renderPillarCard(pillar) {
  const produced = pillar.produced_momentums ?? 0;
  const expected = pillar.expected_momentums ?? 0;
  const item = performance[pillar.owner_address] ||
    pillar.performance_last_30_days || {};
  const rank = pillar.rank === null || pillar.rank === undefined
    ? "—"
    : pillar.rank + 1;
  return '<article class="pillar-card">' +
    '<div class="pillar-card-top">' +
    '<span class="muted">#' + escapeHtml(rank) + '</span>' +
    '<span class="status-badge ' + escapeHtml(pillar.status) + '">' +
    escapeHtml(statusLabel(pillar.status)) + '</span>' +
    '</div>' +
    '<div class="pillar-title-row"><span class="pillar-name">' +
    escapeHtml(pillar.name) + '</span></div>' +
    '<div class="pillar-address">' + escapeHtml(pillar.owner_address) + '</div>' +
    '<div class="pillar-stats">' +
    '<div class="stat-item"><span class="stat-label">Produced / expected</span><strong>' +
    formatNumber(produced) + " / " + formatNumber(expected) + '</strong></div>' +
    '<div class="stat-item"><span class="stat-label">Weight</span><strong>' +
    formatNumber(Math.round((pillar.weight || 0) / 100000000)) + ' ZNN</strong></div>' +
    '<div class="stat-item"><span class="stat-label">' +
    statusDurationLabel(pillar.status) + '</span><strong>' +
    formatDuration(statusDurationSeconds(pillar)) + '</strong></div>' +
    '<div class="stat-item"><span class="stat-label">Momentum / delegate</span><strong>' +
    escapeHtml(pillar.momentum_reward_percentage) + '% / ' +
    escapeHtml(pillar.delegate_reward_percentage) + '%</strong></div>' +
    '</div>' +
    '<div class="stat-item performance-stat">' +
    '<span class="stat-label">Performance · 30d</span>' +
    '<strong class="performance-value' +
    (performanceAvailable(item) ? '' : ' empty') + '">' +
    formatPerformance(item) + '</strong>' +
    '<div class="performance-chart" role="img" aria-label="Daily performance for the last 30 days">' +
    renderPerformanceChart(item) + '</div>' +
    '<div class="performance-chart-axis" aria-hidden="true"><span>30d ago</span><span>Today</span></div>' +
    '</div>' +
    '</article>';
}

function readQuery() {
  const params = new URLSearchParams(window.location.search);
  const status = ["active", "inactive"].includes(params.get("status"))
    ? params.get("status")
    : "";
  const rawPage = Number.parseInt(params.get("page") || "1", 10);
  return {
    status,
    search: (params.get("q") || "").trim(),
    page: Number.isFinite(rawPage) && rawPage > 0 ? rawPage : 1,
  };
}

function buildUrl(nextState) {
  const params = new URLSearchParams();
  if (nextState.status) params.set("status", nextState.status);
  if (nextState.search) params.set("q", nextState.search);
  if (nextState.page > 1) params.set("page", String(nextState.page));
  const query = params.toString();
  return "/pillars" + (query ? "?" + query : "");
}

function syncFilterUi() {
  $("#pillar-search").value = state.search;
  document.querySelectorAll(".filter-tabs a[data-status]").forEach((link) => {
    const active = link.dataset.status === state.status;
    link.classList.toggle("is-active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
    const linkState = { ...state, status: link.dataset.status, page: 1 };
    link.href = buildUrl(linkState);
  });
}

function renderPagination(payload) {
  const target = $("#pillar-pagination");
  const limit = Math.max(1, Number(payload.limit) || pageSize);
  const total = Math.max(0, Number(payload.total) || 0);
  const pageCount = Math.max(1, Math.ceil(total / limit));
  state.page = Math.min(state.page, pageCount);
  if (total <= limit) {
    target.hidden = true;
    target.innerHTML = "";
    return;
  }
  const previousState = { ...state, page: state.page - 1 };
  const nextState = { ...state, page: state.page + 1 };
  const previous = state.page > 1
    ? `<a class="ghost-button pagination-button" data-page="${state.page - 1}" href="${buildUrl(previousState)}">Previous</a>`
    : '<span class="ghost-button pagination-button is-disabled" aria-disabled="true">Previous</span>';
  const next = state.page < pageCount
    ? `<a class="ghost-button pagination-button" data-page="${state.page + 1}" href="${buildUrl(nextState)}">Next</a>`
    : '<span class="ghost-button pagination-button is-disabled" aria-disabled="true">Next</span>';
  target.hidden = false;
  target.innerHTML = previous +
    `<span class="pagination-status">Page ${state.page} of ${pageCount} · ${total} pillars</span>` +
    next;
  target.querySelectorAll("a[data-page]").forEach((link) => {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      history.pushState({}, "", link.href);
      void loadPillars();
    });
  });
}

async function getJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Request failed");
  return payload;
}

async function loadPillars() {
  state.status = readQuery().status;
  state.search = readQuery().search;
  state.page = readQuery().page;
  syncFilterUi();
  const offset = (state.page - 1) * pageSize;
  const query = new URLSearchParams({
    status: state.status,
    q: state.search,
    limit: String(pageSize),
    offset: String(offset),
    performance: "0",
  });
  const list = $("#pillar-list");
  list.setAttribute("aria-busy", "true");
  try {
    const [payload, performancePayload] = await Promise.all([
      getJson(`/api/pillars?${query}`),
      getJson("/api/performance?days=30"),
    ]);
    performance = performancePayload || {};
    const pageCount = Math.max(1, Math.ceil(
      (Number(payload.total) || 0) / (Number(payload.limit) || pageSize)
    ));
    if (state.page > pageCount && Number(payload.total) > 0) {
      const validUrl = buildUrl({ ...state, page: pageCount });
      history.replaceState({}, "", validUrl);
      return loadPillars();
    }
    $("#pillar-count").textContent = `${formatNumber(payload.total)} pillar${payload.total === 1 ? "" : "s"}`;
    list.innerHTML = payload.items?.length
      ? payload.items.map(renderPillarCard).join("")
      : '<div class="empty-state">No pillars found for this filter.</div>';
    renderPagination(payload);
  } catch (error) {
    list.innerHTML = `<div class="empty-state">Could not load pillars: ${escapeHtml(error.message)}</div>`;
    $("#pillar-pagination").hidden = true;
  } finally {
    list.removeAttribute("aria-busy");
  }
}

$("#pillar-filter-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const nextState = {
    ...state,
    search: $("#pillar-search").value.trim(),
    page: 1,
  };
  history.pushState({}, "", buildUrl(nextState));
  void loadPillars();
});

document.querySelectorAll(".filter-tabs a[data-status]").forEach((link) => {
  link.addEventListener("click", (event) => {
    event.preventDefault();
    const nextState = {
      ...state,
      status: link.dataset.status,
      page: 1,
    };
    history.pushState({}, "", buildUrl(nextState));
    void loadPillars();
  });
});

window.addEventListener("popstate", () => void loadPillars());
void loadPillars();
