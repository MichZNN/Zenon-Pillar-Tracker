const historyKind = document.body.dataset.historyKind === "events"
  ? "events"
  : "epochs";
const pageSize = historyKind === "events" ? 25 : 20;
const eventLabels = {
  epoch_available: "New epoch available",
  pillar_created: "Pillar added",
  pillar_dismantled: "Pillar dismantled",
  pillar_name_changed: "Name changed",
  reward_shares_changed: "Reward shares changed",
  pillar_inactive: "Pillar inactive",
  pillar_active: "Pillar active",
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

function formatNumber(value) {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("en-US").format(value);
}

function eventLabel(type) {
  return eventLabels[type] || type || "Event";
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

function readQuery() {
  const params = new URLSearchParams(window.location.search);
  const rawPage = Number.parseInt(params.get("page") || "1", 10);
  return {
    page: Number.isFinite(rawPage) && rawPage > 0 ? rawPage : 1,
    eventType: historyKind === "events" ? (params.get("type") || "") : "",
  };
}

function buildUrl(page, eventType) {
  const params = new URLSearchParams();
  if (page > 1) params.set("page", String(page));
  if (historyKind === "events" && eventType) params.set("type", eventType);
  const query = params.toString();
  return `/${historyKind}${query ? "?" + query : ""}`;
}

function renderEpochs(items) {
  const target = $("#history-list");
  if (!items.length) {
    target.innerHTML = '<div class="empty-state">No epoch data yet.</div>';
    return;
  }
  target.innerHTML = items.map((epoch) => {
    const start = epoch.epoch_start_at
      ? formatDate(epoch.epoch_start_at)
      : "Not available";
    const estimate = epoch.epoch_start_inferred
      ? '<span class="epoch-estimated">Estimated</span>'
      : "";
    return '<article class="history-card">' +
      '<div class="history-card-heading">' +
      '<strong>Epoch ' + escapeHtml(epoch.epoch) + '</strong>' +
      '<span class="event-time">Last seen ' + formatDate(epoch.last_seen_at) + '</span>' +
      '</div>' +
      '<div class="history-card-grid">' +
      '<div><span>Started</span><strong>' + start + estimate + '</strong></div>' +
      '<div><span>First seen</span><strong>' + formatDate(epoch.first_seen_at) + '</strong></div>' +
      '<div><span>ZNN reward</span><strong>' + formatNumber(epoch.znn_reward) + '</strong></div>' +
      '<div><span>QSR reward</span><strong>' + formatNumber(epoch.qsr_reward) + '</strong></div>' +
      '<div><span>Momentum height</span><strong>' + formatNumber(epoch.last_observed_momentum_height) + '</strong></div>' +
      '<div><span>Announcement</span><strong>' + formatDate(epoch.announcement_at) + '</strong></div>' +
      '</div>' +
      '</article>';
  }).join("");
}

function renderEvents(items) {
  const target = $("#history-list");
  if (!items.length) {
    target.innerHTML = '<div class="empty-state">No events found.</div>';
    return;
  }
  target.innerHTML = items.map((event) =>
    '<article class="history-card event-history-card">' +
    '<div class="history-card-heading">' +
    '<strong class="event-type">' + escapeHtml(eventLabel(event.event_type)) + '</strong>' +
    '<span class="event-time">' + formatDate(event.observed_at) + '</span>' +
    '</div>' +
    '<p class="event-detail">' + escapeHtml(eventDescription(event)) + '</p>' +
    '<div class="history-card-meta">' +
    '<span>Epoch ' + escapeHtml(event.epoch ?? "—") + '</span>' +
    '<span>Momentum ' + escapeHtml(event.momentum_height ?? "—") + '</span>' +
    '</div>' +
    '</article>'
  ).join("");
}

function renderPagination(payload, query) {
  const target = $("#history-pagination");
  const limit = Math.max(1, Number(payload.limit) || pageSize);
  const total = Math.max(0, Number(payload.total) || 0);
  const pageCount = Math.max(1, Math.ceil(total / limit));
  const page = query.page;
  if (total <= limit) {
    target.hidden = true;
    target.innerHTML = "";
    return;
  }
  const previous = page > 1
    ? `<a class="ghost-button pagination-button" data-page="${page - 1}" href="${buildUrl(page - 1, query.eventType)}">Previous</a>`
    : '<span class="ghost-button pagination-button is-disabled" aria-disabled="true">Previous</span>';
  const next = page < pageCount
    ? `<a class="ghost-button pagination-button" data-page="${page + 1}" href="${buildUrl(page + 1, query.eventType)}">Next</a>`
    : '<span class="ghost-button pagination-button is-disabled" aria-disabled="true">Next</span>';
  target.hidden = false;
  target.innerHTML = previous +
    `<span class="pagination-status">Page ${page} of ${pageCount} · ${total} records</span>` +
    next;
  target.querySelectorAll("a[data-page]").forEach((link) => {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      const nextPage = Number(link.dataset.page);
      history.pushState({}, "", link.href);
      void loadPage(nextPage);
    });
  });
}

async function getJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Request failed");
  return payload;
}

async function loadPage(requestedPage = readQuery().page) {
  const current = readQuery();
  const page = Math.max(1, Number(requestedPage) || 1);
  const offset = (page - 1) * pageSize;
  const params = new URLSearchParams({
    limit: String(pageSize),
    offset: String(offset),
    paged: "1",
  });
  if (historyKind === "events" && current.eventType) {
    params.set("type", current.eventType);
  }
  const target = $("#history-list");
  target.setAttribute("aria-busy", "true");
  try {
    const payload = await getJson(`/api/${historyKind}?${params}`);
    const pageCount = Math.max(1, Math.ceil(
      (Number(payload.total) || 0) / (Number(payload.limit) || pageSize)
    ));
    if (page > pageCount && Number(payload.total) > 0) {
      const validUrl = buildUrl(pageCount, current.eventType);
      history.replaceState({}, "", validUrl);
      return loadPage(pageCount);
    }
    if (historyKind === "events") renderEvents(payload.items || []);
    else renderEpochs(payload.items || []);
    renderPagination(payload, { ...current, page });
  } catch (error) {
    target.innerHTML = `<div class="empty-state">Could not load history: ${escapeHtml(error.message)}</div>`;
    $("#history-pagination").hidden = true;
  } finally {
    target.removeAttribute("aria-busy");
  }
}

if (historyKind === "events") {
  const filter = $("#event-type-filter");
  const query = readQuery();
  filter.value = query.eventType;
  filter.addEventListener("change", () => {
    const nextUrl = buildUrl(1, filter.value);
    history.pushState({}, "", nextUrl);
    void loadPage(1);
  });
}

window.addEventListener("popstate", () => void loadPage());
void loadPage();
