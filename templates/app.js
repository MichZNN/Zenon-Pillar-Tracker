const state = {
  search: "",
  status: "",
};

const $ = (selector) => document.querySelector(selector);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
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

function formatNumber(value) {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("en-US").format(value);
}

function formatEpoch(value) {
  if (value === null || value === undefined) return "—";
  return String(value);
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

function eventLabel(type) {
  const labels = {
    epoch_available: "New epoch available",
    pillar_created: "Pillar added",
    pillar_dismantled: "Pillar dismantled",
    pillar_name_changed: "Name changed",
    reward_shares_changed: "Reward shares changed",
    pillar_inactive: "Pillar inactive",
    pillar_active: "Pillar active",
  };
  return labels[type] || type || "Event";
}

async function getJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Request failed");
  return payload;
}

function renderHealth(collector) {
  const state = collector?.state || "unknown";
  const badge = $("#health-badge");
  const label = collector?.label || "Waiting for tracker";
  badge.className = "health-badge " + state;
  badge.textContent = label;
  badge.title = collector?.description || label;
  badge.setAttribute("aria-label", "Tracker status: " + label);
}

function renderOverview(payload) {
  const epoch = payload.epoch;
  const node = payload.node;
  const counts = payload.pillar_counts || {};
  $("#current-epoch").textContent = epoch ? formatEpoch(epoch.epoch) : "—";
  $("#momentum-height").textContent = node?.last_momentum_height
    ? formatNumber(node.last_momentum_height)
    : "—";
  $("#last-update").textContent = node?.last_success_at
    ? "Last successful update " + formatDate(node.last_success_at)
    : "No successful update yet";
  $("#pillar-total").textContent = formatNumber(counts.total || 0);
  $("#pillar-active").textContent = formatNumber(counts.active || 0);
  $("#pillar-inactive").textContent = formatNumber(counts.inactive || 0);
  $("#footer-updated").textContent = payload.last_snapshot_at
    ? "Latest snapshot: " + formatDate(payload.last_snapshot_at)
    : "No snapshot yet";
  renderHealth(payload.collector);
  renderEvents(payload.recent_events || []);
}

function renderPillars(payload) {
  $("#pillar-count").textContent = formatNumber(payload.total) + " records";
  const list = $("#pillar-list");
  if (!payload.items?.length) {
    list.innerHTML = '<div class="empty-state">No pillars found.</div>';
    return;
  }
  list.innerHTML = payload.items.map((pillar) => {
    const produced = pillar.produced_momentums ?? 0;
    const expected = pillar.expected_momentums ?? 0;
    const performance = pillar.performance_last_30_days || {};
    const rank = pillar.rank === null || pillar.rank === undefined
      ? "—"
      : pillar.rank + 1;
    return '<article class="pillar-card">' +
      '<div class="pillar-card-top">' +
      '<span class="muted">#' + rank + '</span>' +
      '<span class="status-badge ' + escapeHtml(pillar.status) + '">' +
      statusLabel(pillar.status) + '</span>' +
      '</div>' +
      '<div class="pillar-title-row">' +
      '<span class="pillar-name" data-owner="' +
      escapeHtml(pillar.owner_address) + '">' +
      escapeHtml(pillar.name) + '</span>' +
      '</div>' +
      '<div class="pillar-address">' + escapeHtml(pillar.owner_address) + '</div>' +
      '<div class="pillar-stats">' +
      '<div class="stat-item"><span class="stat-label">Produced / expected</span><strong>' +
      formatNumber(produced) + " / " + formatNumber(expected) + '</strong></div>' +
      '<div class="stat-item"><span class="stat-label">Weight</span><strong>' +
      formatNumber(Math.round((pillar.weight || 0) / 100000000)) +
      ' ZNN</strong></div>' +
      '<div class="stat-item"><span class="stat-label">' +
      statusDurationLabel(pillar.status) + '</span><strong>' +
      formatDuration(statusDurationSeconds(pillar)) + '</strong></div>' +
      '<div class="stat-item"><span class="stat-label">Momentum / delegate</span><strong>' +
      pillar.momentum_reward_percentage + '% / ' +
      pillar.delegate_reward_percentage + '%</strong></div>' +
      '</div>' +
      '<div class="stat-item performance-stat">' +
      '<span class="stat-label">Performance · 30d</span>' +
      '<strong class="performance-value' +
      (performanceAvailable(performance) ? '' : ' empty') + '">' +
      formatPerformance(performance) + '</strong>' +
      '<div class="performance-chart" role="img" ' +
      'aria-label="Daily performance for the last 30 days">' +
      renderPerformanceChart(performance) +
      '</div>' +
      '<div class="performance-chart-axis" aria-hidden="true">' +
      '<span>30d ago</span><span>Today</span>' +
      '</div>' +
      '</div>' +
      '</article>';
  }).join("");
  list.querySelectorAll("[data-owner]").forEach((element) => {
    element.addEventListener("click", () => showPillar(element.dataset.owner));
  });
}

function renderEpochs(epochs) {
  const target = $("#epoch-list");
  if (!epochs.length) {
    target.innerHTML = '<div class="empty-state">No epoch data yet.</div>';
    return;
  }
  target.innerHTML = epochs.map((epoch) => {
    const timeLabel = formatDate(epoch.epoch_start_at);
    const estimateLabel = epoch.epoch_start_inferred
      ? '<span class="epoch-estimated">Estimated</span>'
      : '';
    return '<div class="epoch-row">' +
    '<div class="event-top">' +
    '<span class="epoch-number">Epoch ' + formatEpoch(epoch.epoch) + '</span>' +
    '<span class="event-time">' + timeLabel + estimateLabel + '</span>' +
    '</div>' +
    '</div>';
  }).join("");
}

function eventDescription(event) {
  const details = event.details || {};
  if (event.event_type === "epoch_available") {
    if (details.inferred) {
      return "Epoch " + event.epoch + " · approximate date from Telegram gap";
    }
    return "Rewards for epoch " + event.epoch + " are available to collect";
  }
  if (event.event_type === "pillar_name_changed") {
    return (details.old_name || "—") + " → " + (details.new_name || "—");
  }
  if (event.event_type === "reward_shares_changed") {
    return "Reward sharing percentages updated";
  }
  if (event.event_type === "pillar_inactive") {
    const name = details.name || event.owner_address || "Unknown pillar";
    if (details.missed_momentums === undefined || details.missed_momentums === null) {
      return name;
    }
    return name + " · " + details.missed_momentums + " missed checks";
  }
  return details.name || event.owner_address || "Network event";
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

function formatPerformance(performance) {
  if (!performanceAvailable(performance)) return "—";
  const percentage = Number(performance?.percentage);
  return percentage.toFixed(1) + "%";
}

function performanceAvailable(performance) {
  const percentage = Number(performance?.percentage);
  return Number.isFinite(percentage) && Number(performance?.expected) > 0;
}

function formatPerformanceDay(value) {
  if (!value) return "Unknown date";
  const date = new Date(value + "T00:00:00Z");
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  }).format(date);
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

function renderPerformanceChart(performance) {
  const points = Array.isArray(performance?.daily)
    ? performance.daily
    : [];
  if (!points.length) {
    return '<span class="performance-chart-empty">Daily data is not available yet.</span>';
  }
  return points.map((point) => {
    const date = formatPerformanceDay(point.date);
    const label = performanceAvailable(point)
      ? date + ": " + Number(point.percentage).toFixed(1) + "%"
      : date + ": No data";
    return '<span class="performance-bar ' +
      performanceBarClass(point) +
      '" style="height:' + performanceBarHeight(point) + '%" ' +
      'title="' + escapeHtml(label) + '" aria-hidden="true"></span>';
  }).join("");
}

function renderEvents(events) {
  const target = $("#event-list");
  if (!events.length) {
    target.innerHTML = '<div class="empty-state">No events yet.</div>';
    return;
  }
  target.innerHTML = events.map((event) =>
    '<div class="event-row">' +
    '<div class="event-top">' +
    '<span class="event-type">' + escapeHtml(eventLabel(event.event_type)) + '</span>' +
    '<span class="event-time">' + formatDate(event.observed_at) + '</span>' +
    '</div>' +
    '<div class="event-detail">' +
    escapeHtml(eventDescription(event)) +
    '</div>' +
    '</div>'
  ).join("");
}

async function showPillar(ownerAddress) {
  try {
    const pillar = await getJson(
      "/api/pillars/" + encodeURIComponent(ownerAddress)
    );
    $("#pillar-detail").hidden = false;
    $("#detail-name").textContent = pillar.name;
    const detailRank = pillar.rank === null || pillar.rank === undefined
      ? "—"
      : pillar.rank + 1;
    $("#detail-summary").innerHTML =
      '<div class="detail-stat"><span>Status</span><strong>' +
      statusLabel(pillar.status) + '</strong></div>' +
      '<div class="detail-stat"><span>' + statusDurationLabel(pillar.status) +
      '</span><strong>' + formatDuration(statusDurationSeconds(pillar)) + '</strong></div>' +
      '<div class="detail-stat"><span>Performance (30 days)</span><strong>' +
      formatPerformance(pillar.performance_last_30_days || {}) + '</strong></div>' +
      '<div class="detail-stat"><span>Rank</span><strong>' +
      detailRank + '</strong></div>' +
      '<div class="detail-stat"><span>Momentum share</span><strong>' +
      pillar.momentum_reward_percentage + '%</strong></div>' +
      '<div class="detail-stat"><span>Delegate share</span><strong>' +
      pillar.delegate_reward_percentage + '%</strong></div>';
    $("#detail-history").innerHTML = (pillar.history || []).map((row) =>
      '<tr>' +
      '<td>' + formatDate(row.observed_at) + '</td>' +
      '<td><span class="status-badge ' + escapeHtml(row.status) + '">' +
      statusLabel(row.status) + '</span></td>' +
      '<td>' + formatNumber(row.produced_momentums) + '</td>' +
      '<td>' + formatNumber(row.expected_momentums) + '</td>' +
      '<td>' + formatNumber(Math.round((row.weight || 0) / 100000000)) +
      ' ZNN</td>' +
      '</tr>'
    ).join("") || '<tr><td colspan="5">No history yet.</td></tr>';
    $("#pillar-detail").scrollIntoView({
      behavior: "smooth",
      block: "start",
    });
  } catch (error) {
    console.error(error);
  }
}

async function refresh() {
  try {
    const [overview, pillars, epochs] = await Promise.all([
      getJson("/api/overview"),
      getJson(
        "/api/pillars?status=" + encodeURIComponent(state.status) +
        "&q=" + encodeURIComponent(state.search)
      ),
      getJson("/api/epochs?limit=11"),
    ]);
    renderOverview(overview);
    renderPillars(pillars);
    renderEpochs(epochs);
  } catch (error) {
    console.error(error);
    $("#health-badge").className = "health-badge error";
    $("#health-badge").textContent = "Dashboard error";
  }
}

$("#pillar-search").addEventListener("input", (event) => {
  state.search = event.target.value;
  refresh();
});
$("#status-filter").addEventListener("change", (event) => {
  state.status = event.target.value;
  refresh();
});
$("#close-detail").addEventListener("click", () => {
  $("#pillar-detail").hidden = true;
});

refresh();
window.setInterval(refresh, 30000);
