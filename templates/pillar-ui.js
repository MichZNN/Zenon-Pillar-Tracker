/*
 * Shared helpers for the dashboard, the pillar directory, the pillar page
 * and the history pages: formatting, relative times, performance charts,
 * pillar cards and favorites.
 */

const $ = (selector) => document.querySelector(selector);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function getJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Request failed");
  return payload;
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

function formatRelativeTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const seconds = Math.round((Date.now() - date.getTime()) / 1000);
  if (seconds < 0) return "in the future";
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return minutes + "m ago";
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return hours + "h ago";
  const days = Math.floor(hours / 24);
  if (days < 30) return days + "d ago";
  return formatDate(value);
}

// A relative time ("3h ago") that shows the exact UTC time on hover, and
// switches to it when tapped (see the click handler at the bottom).
function renderTime(value, className = "event-time") {
  if (!value) return '<span class="' + className + '">—</span>';
  const absolute = formatDate(value);
  const relative = formatRelativeTime(value);
  return '<time class="' + className + ' rel-time" datetime="' +
    escapeHtml(value) + '" title="' + escapeHtml(absolute) + '" ' +
    'data-relative="' + escapeHtml(relative) + '" data-absolute="' +
    escapeHtml(absolute) + '" tabindex="0" role="button">' +
    escapeHtml(relative) + '</time>';
}

function formatNumber(value) {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("en-US").format(value);
}

function formatEpoch(value) {
  if (value === null || value === undefined) return "—";
  return String(value);
}

function formatWeight(weight) {
  return formatNumber(Math.round((weight || 0) / 100000000)) + " ZNN";
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

function formatRank(rank) {
  return rank === null || rank === undefined ? "—" : String(rank + 1);
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

const EVENT_LABELS = {
  epoch_available: "New epoch available",
  pillar_created: "Pillar added",
  pillar_dismantled: "Pillar dismantled",
  pillar_name_changed: "Name changed",
  reward_shares_changed: "Reward shares changed",
  pillar_inactive: "Pillar inactive",
  pillar_active: "Pillar active",
};

function eventLabel(type) {
  return EVENT_LABELS[type] || type || "Event";
}

// Colour events like the status badges: good news green, bad news pink.
function eventTone(type) {
  if (type === "pillar_active" || type === "pillar_created") return "tone-positive";
  if (type === "pillar_inactive" || type === "pillar_dismantled") return "tone-negative";
  return "tone-neutral";
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

function pillarUrl(ownerAddress) {
  return "/pillar/" + encodeURIComponent(ownerAddress);
}

/* ---------- Performance ---------- */

// Bars run from this percentage (bottom) to 100% (top), so the difference
// between a 75% and a 98% day is clearly visible.
const PERFORMANCE_CHART_FLOOR = 50;

function performanceAvailable(item) {
  const percentage = Number(item?.percentage);
  return Number.isFinite(percentage) && Number(item?.expected) > 0;
}

function formatPerformance(item) {
  return performanceAvailable(item)
    ? Number(item.percentage).toFixed(1) + "%"
    : "—";
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
  const percentage = Math.min(100, Number(point.percentage));
  const scaled = (percentage - PERFORMANCE_CHART_FLOOR) /
    (100 - PERFORMANCE_CHART_FLOOR) * 100;
  return Math.max(6, Math.min(100, scaled));
}

// Days are UTC calendar days. Today and the day a pillar went live only
// cover part of the day, so their bars are drawn lighter and labelled.
function partialDayNote(point, pillar) {
  if (!point?.date) return null;
  if (point.date === new Date().toISOString().slice(0, 10)) {
    return "partial day (today so far)";
  }
  const liveSince = pillar?.live_since ? new Date(pillar.live_since) : null;
  if (liveSince && !Number.isNaN(liveSince.getTime()) &&
      liveSince.toISOString().slice(0, 10) === point.date &&
      liveSince.toISOString().slice(11, 16) !== "00:00") {
    return "partial day (live since " + liveSince.toISOString().slice(11, 16) + " UTC)";
  }
  return null;
}

function performanceDayLabel(point, pillar = null) {
  const date = formatPerformanceDay(point.date);
  const note = partialDayNote(point, pillar);
  const suffix = note ? " · " + note : "";
  if (!performanceAvailable(point)) return date + ": No data" + suffix;
  return date + ": " + Number(point.percentage).toFixed(1) + "% · " +
    formatNumber(point.produced) + " of " + formatNumber(point.expected) +
    " momentums" + suffix;
}

function performanceBarClasses(point, pillar = null) {
  return performanceBarClass(point) +
    (partialDayNote(point, pillar) ? " is-partial" : "");
}

function renderPerformanceChart(performance, loading = false, pillar = null) {
  if (loading) {
    return '<span class="skeleton skeleton-performance-chart" aria-hidden="true"></span>';
  }
  const points = Array.isArray(performance?.daily) ? performance.daily : [];
  if (!points.length) {
    return '<span class="performance-chart-empty">Daily data is not available yet.</span>';
  }
  return points.map((point) =>
    '<span class="performance-bar ' + performanceBarClasses(point, pillar) +
    '" style="height:' + performanceBarHeight(point) + '%" ' +
    'title="' + escapeHtml(performanceDayLabel(point, pillar)) + '" aria-hidden="true"></span>'
  ).join("");
}

/* ---------- Favorites ---------- */

const FAVORITES_KEY = "pillarTracker.favorites";

function getFavorites() {
  try {
    const stored = JSON.parse(window.localStorage.getItem(FAVORITES_KEY) || "[]");
    return new Set(Array.isArray(stored) ? stored.filter((item) => typeof item === "string") : []);
  } catch (error) {
    return new Set();
  }
}

function isFavorite(ownerAddress) {
  return getFavorites().has(ownerAddress);
}

function toggleFavorite(ownerAddress) {
  const favorites = getFavorites();
  if (favorites.has(ownerAddress)) favorites.delete(ownerAddress);
  else favorites.add(ownerAddress);
  try {
    window.localStorage.setItem(FAVORITES_KEY, JSON.stringify([...favorites]));
  } catch (error) {
    // Storage can be unavailable (private mode); the star still toggles
    // for this page view.
  }
  window.dispatchEvent(new CustomEvent("favoriteschange", {
    detail: { ownerAddress, favorite: favorites.has(ownerAddress) },
  }));
  return favorites.has(ownerAddress);
}

function favoriteButton(pillar) {
  const favorite = isFavorite(pillar.owner_address);
  const label = (favorite ? "Remove " : "Add ") + (pillar.name || "pillar") +
    (favorite ? " from favorites" : " to favorites");
  return '<button class="favorite-button' + (favorite ? ' is-favorite' : '') +
    '" type="button" data-favorite="' + escapeHtml(pillar.owner_address) +
    '" data-name="' + escapeHtml(pillar.name || "pillar") + '" aria-pressed="' +
    favorite + '" aria-label="' + escapeHtml(label) + '" title="' +
    escapeHtml(label) + '"><i class="' + (favorite ? 'fa-solid' : 'fa-regular') +
    ' fa-star" aria-hidden="true"></i></button>';
}

function syncFavoriteButtons(ownerAddress, favorite) {
  document.querySelectorAll(".favorite-button").forEach((button) => {
    if (button.dataset.favorite !== ownerAddress) return;
    const name = button.dataset.name || "pillar";
    const label = (favorite ? "Remove " : "Add ") + name +
      (favorite ? " from favorites" : " to favorites");
    button.classList.toggle("is-favorite", favorite);
    button.setAttribute("aria-pressed", String(favorite));
    button.setAttribute("aria-label", label);
    button.title = label;
    const icon = button.querySelector("i");
    if (icon) icon.className = (favorite ? "fa-solid" : "fa-regular") + " fa-star";
  });
}

/* ---------- Pillar card ---------- */

function renderPillarCard(pillar, performance, options = {}) {
  const loading = Boolean(options.loading);
  const produced = pillar.produced_momentums ?? 0;
  const expected = pillar.expected_momentums ?? 0;
  const item = performance || pillar.performance_last_30_days || {};
  const favorite = isFavorite(pillar.owner_address);
  return '<article class="pillar-card' + (favorite ? ' is-favorite' : '') + '">' +
    '<div class="pillar-card-top">' +
    '<span class="muted">#' + escapeHtml(formatRank(pillar.rank)) + '</span>' +
    '<div class="pillar-card-actions">' +
    favoriteButton(pillar) +
    '<span class="status-badge ' + escapeHtml(pillar.status) + '">' +
    escapeHtml(statusLabel(pillar.status)) + '</span>' +
    '</div>' +
    '</div>' +
    '<div class="pillar-title-row">' +
    '<a class="pillar-name pillar-card-link" href="' + pillarUrl(pillar.owner_address) + '">' +
    escapeHtml(pillar.name) + '</a>' +
    '</div>' +
    '<div class="pillar-address">' + escapeHtml(pillar.owner_address) + '</div>' +
    '<div class="pillar-stats">' +
    '<div class="stat-item"><span class="stat-label">Produced / expected</span><strong>' +
    formatNumber(produced) + " / " + formatNumber(expected) + '</strong></div>' +
    '<div class="stat-item"><span class="stat-label">Weight</span><strong>' +
    formatWeight(pillar.weight) + '</strong></div>' +
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
    (!loading && !performanceAvailable(item) ? ' empty' : '') + '">' +
    (loading
      ? '<span class="skeleton skeleton-performance-value" aria-hidden="true"></span>'
      : formatPerformance(item)) +
    '</strong>' +
    '<div class="performance-chart" role="img" aria-label="' +
    (loading ? 'Loading daily performance' : 'Daily performance for the last 30 days') + '"' +
    (loading ? ' aria-busy="true"' : '') + '>' +
    renderPerformanceChart(item, loading, pillar) +
    '</div>' +
    '<div class="performance-chart-axis" aria-hidden="true">' +
    '<span>30d ago</span><span>Today</span>' +
    '</div>' +
    '</div>' +
    '</article>';
}

// Favorites first, otherwise keep the order the server returned.
function favoritesFirst(items) {
  const favorites = getFavorites();
  if (!favorites.size) return items;
  return [
    ...items.filter((item) => favorites.has(item.owner_address)),
    ...items.filter((item) => !favorites.has(item.owner_address)),
  ];
}

const PILLAR_SORTS = ["rank", "performance", "weight", "name"];

/* ---------- Shared interactions ---------- */

document.addEventListener("click", (event) => {
  const favorite = event.target.closest(".favorite-button");
  if (favorite) {
    event.preventDefault();
    event.stopPropagation();
    const ownerAddress = favorite.dataset.favorite;
    syncFavoriteButtons(ownerAddress, toggleFavorite(ownerAddress));
    return;
  }
  const time = event.target.closest(".rel-time");
  if (time) toggleTime(time);
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") return;
  const time = event.target.closest?.(".rel-time");
  if (!time) return;
  event.preventDefault();
  toggleTime(time);
});

function toggleTime(element) {
  const showAbsolute = element.textContent === element.dataset.relative;
  element.textContent = showAbsolute
    ? element.dataset.absolute
    : element.dataset.relative;
  element.classList.toggle("is-absolute", showAbsolute);
}
