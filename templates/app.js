const state = {
  search: "",
  status: "",
  sort: "rank",
  pillars: null,
  performance: {},
  performanceLoading: true,
  refreshId: 0,
};
let performanceRequest = null;

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
  // Full text on desktop; a short relative time on mobile.
  $("#last-update").innerHTML = node?.last_success_at
    ? '<span class="desktop-only">Last successful update ' +
      escapeHtml(formatDate(node.last_success_at)) + '</span>' +
      '<span class="mobile-only" title="' + escapeHtml(formatDate(node.last_success_at)) +
      '">Updated ' + escapeHtml(formatRelativeTime(node.last_success_at)) + '</span>'
    : "No successful update yet";
  $("#pillar-total").textContent = formatNumber(counts.total || 0);
  $("#pillar-active").textContent = formatNumber(counts.active || 0);
  $("#pillar-inactive").textContent = formatNumber(counts.inactive || 0);
  $("#footer-updated").textContent = payload.last_snapshot_at
    ? "Latest snapshot: " + formatDate(payload.last_snapshot_at)
    : "No snapshot yet";
  renderEvents(payload.recent_events || []);
  updateListToggle("event-list");
}

function renderPillars(payload) {
  const total = Number(payload.total) || 0;
  $("#pillar-count").textContent = formatNumber(total) +
    " pillar" + (total === 1 ? "" : "s");
  const pillarsLink = $("#pillars-link");
  if (pillarsLink) {
    const query = new URLSearchParams();
    if (state.status && state.status !== "all") query.set("status", state.status);
    if (state.search) query.set("q", state.search);
    if (state.sort && state.sort !== "rank") query.set("sort", state.sort);
    const queryString = query.toString();
    pillarsLink.href = "/pillars" + (queryString ? "?" + queryString : "");
  }
  const list = $("#pillar-list");
  if (!payload.items?.length) {
    list.innerHTML = '<div class="empty-state">No pillars found.</div>';
    return;
  }
  list.innerHTML = favoritesFirst(payload.items).map((pillar) =>
    renderPillarCard(
      pillar,
      state.performance[pillar.owner_address] || pillar.performance_last_30_days || {},
      { loading: state.performanceLoading },
    )
  ).join("");
}

const MOBILE_COLLAPSED_ROWS = 3;

function updateListToggle(listId) {
  const list = $("#" + listId);
  const section = list.closest(".collapsible-section");
  const toggle = section?.querySelector(".list-toggle");
  if (!toggle) return;
  const rows = list.querySelectorAll(".epoch-row, .event-row").length;
  toggle.hidden = rows <= MOBILE_COLLAPSED_ROWS;
}

document.querySelectorAll(".collapsible-section .list-toggle").forEach((toggle) => {
  toggle.addEventListener("click", () => {
    const section = toggle.closest(".collapsible-section");
    const expanded = section.classList.toggle("is-expanded");
    toggle.setAttribute("aria-expanded", String(expanded));
    toggle.querySelector(".list-toggle-label").textContent =
      expanded ? "Show less" : "Show more";
  });
});

function renderEpochs(epochs) {
  const target = $("#epoch-list");
  if (!epochs.length) {
    target.innerHTML = '<div class="empty-state">No epoch data yet.</div>';
    return;
  }
  target.innerHTML = epochs.map((epoch) => {
    const timeLabel = renderTime(epoch.epoch_start_at);
    const estimateLabel = epoch.epoch_start_inferred
      ? '<span class="epoch-estimated">Estimated</span>'
      : '';
    return '<div class="epoch-row">' +
    '<div class="event-top">' +
    '<span class="epoch-number">Epoch ' + formatEpoch(epoch.epoch) + '</span>' +
    '<span class="event-time-group">' + timeLabel + estimateLabel + '</span>' +
    '</div>' +
    '</div>';
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
    '<span class="event-type ' + eventTone(event.event_type) + '">' +
    escapeHtml(eventLabel(event.event_type)) + '</span>' +
    renderTime(event.observed_at) +
    '</div>' +
    '<div class="event-detail">' +
    escapeHtml(eventDescription(event)) +
    '</div>' +
    '</div>'
  ).join("");
}

async function refreshCollectorStatus() {
  try {
    const collector = await getJson("/api/collector-status");
    renderHealth(collector);
  } catch (error) {
    console.error(error);
    renderHealth({
      state: "unknown",
      label: "Status unavailable",
      description: "The tracker status could not be loaded.",
    });
  }
}

async function refreshPerformance(refreshId) {
  try {
    if (!performanceRequest) {
      performanceRequest = getJson("/api/performance?days=30").finally(() => {
        performanceRequest = null;
      });
    }
    const performance = await performanceRequest;
    if (refreshId !== state.refreshId) return;
    state.performance = performance || {};
  } catch (error) {
    console.error(error);
  } finally {
    if (refreshId !== state.refreshId) return;
    state.performanceLoading = false;
    if (state.pillars) renderPillars(state.pillars);
  }
}

async function refresh() {
  const refreshId = ++state.refreshId;
  try {
    const [overview, pillars, epochs] = await Promise.all([
      getJson("/api/overview"),
      getJson(
        "/api/pillars?status=" + encodeURIComponent(state.status) +
        "&q=" + encodeURIComponent(state.search) +
        "&sort=" + encodeURIComponent(state.sort) +
        "&performance=0"
      ),
      getJson("/api/epochs?limit=11"),
    ]); 
    if (refreshId !== state.refreshId) return;
    state.pillars = pillars;
    // Keep showing the previous chart during background refreshes; only the
    // first load shows the skeleton.
    state.performanceLoading = !Object.keys(state.performance).length;
    renderOverview(overview);
    renderPillars(pillars);
    renderEpochs(epochs);
    updateListToggle("epoch-list");
    void refreshPerformance(refreshId);
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
$("#sort-filter").addEventListener("change", (event) => {
  state.sort = PILLAR_SORTS.includes(event.target.value) ? event.target.value : "rank";
  refresh();
});
// Re-order the list when a star is toggled so favorites move to the top.
window.addEventListener("favoriteschange", () => {
  if (state.pillars) renderPillars(state.pillars);
});

refresh();
refreshCollectorStatus();
window.setInterval(refresh, 30000);
window.setInterval(refreshCollectorStatus, 30000);
