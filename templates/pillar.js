const ownerAddress = decodeURIComponent(
  window.location.pathname.replace(/^\/pillar\//, "").replace(/\/$/, "")
);
const EVENTS_COLLAPSED = 8;
const pageState = {
  pillar: null,
  selectedDay: null,
  eventsExpanded: false,
};

function setHeaderTitle(name) {
  // The site header keeps the tracker name; the pillar name is the
  // heading of the overview block.
  const title = document.getElementById("pillar-title");
  if (title) title.textContent = name;
  document.title = name + " · Zenon Pillar Tracker";
}

function renderSummary(pillar) {
  const performance = pillar.performance_last_30_days || {};
  $("#pillar-rank").textContent = pillar.rank === null || pillar.rank === undefined
    ? "Unranked"
    : "Rank #" + formatRank(pillar.rank);
  $("#pillar-identity").hidden = false;
  $("#pillar-address").innerHTML =
    '<span class="pillar-address-text">' + escapeHtml(pillar.owner_address) + '</span>' +
    '<button class="icon-button" type="button" data-copy="' +
    escapeHtml(pillar.owner_address) + '" aria-label="Copy address" title="Copy address">' +
    '<i class="fa-regular fa-copy" aria-hidden="true"></i></button>';
  $("#pillar-actions").innerHTML =
    favoriteButton(pillar) +
    '<button class="icon-button share-button" type="button" aria-label="Share this pillar" title="Share this pillar">' +
    '<i class="fa-solid fa-share-nodes" aria-hidden="true"></i></button>';
  $("#pillar-summary").innerHTML =
    '<div class="pillar-stats pillar-page-stats">' +
    renderStatusTile(pillar) +
    '<div class="stat-item"><span class="stat-label">Performance · 30d</span><strong class="performance-value' +
    (performanceAvailable(performance) ? '' : ' empty') + '">' +
    formatPerformance(performance) + '</strong>' +
    '<span class="stat-sub">Of expected momentums</span></div>' +
    renderAprTile(pillar, performance) +
    '<div class="stat-item"><span class="stat-label">This epoch</span><strong>' +
    formatNumber(pillar.produced_momentums ?? 0) + ' / ' +
    formatNumber(pillar.expected_momentums ?? 0) + '</strong>' +
    '<span class="stat-sub">Produced / expected</span></div>' +
    '<div class="stat-item"><span class="stat-label">Momentums / day · 7d</span><strong>' +
    (performance.delegator_apr ? formatNumber(performance.delegator_apr.momentums_per_day) : 'Not available') +
    '</strong><span class="stat-sub">Completed-day average</span></div>' +
    '<div class="stat-item"><span class="stat-label">Weight</span><strong>' +
    formatWeight(pillar.weight) + '</strong>' +
    '<span class="stat-sub">Delegated</span></div>' +
    '</div>';
}

function renderStatusTile(pillar) {
  const seconds = statusDurationSeconds(pillar);
  const duration = seconds === null || seconds === undefined ? "" : formatDuration(seconds);
  return '<div class="stat-item stat-status ' + escapeHtml(pillar.status) + '">' +
    '<span class="stat-label">Status</span>' +
    '<strong><i class="status-dot" aria-hidden="true"></i>' +
    escapeHtml(statusLabel(pillar.status)) + '</strong>' +
    '<span class="stat-sub">' +
    (duration ? escapeHtml(statusDurationLabel(pillar.status) + " " + duration) : "&nbsp;") +
    '</span></div>';
}

function renderChart(pillar) {
  const performance = pillar.performance_last_30_days || {};
  const points = Array.isArray(performance.daily) ? performance.daily : [];
  $("#pillar-performance-total").textContent = formatPerformance(performance);
  $("#pillar-performance-total").classList.toggle(
    "empty",
    !performanceAvailable(performance),
  );
  const chart = $("#pillar-performance-chart");
  if (!points.length) {
    chart.innerHTML = '<span class="performance-chart-empty">Daily data is not available yet.</span>';
    $("#performance-readout").textContent = "";
    return;
  }
  chart.innerHTML = points.map((point, index) =>
    '<button class="performance-bar-button' +
    (pageState.selectedDay === point.date ? ' is-selected' : '') +
    '" type="button" data-index="' + index + '" aria-label="' +
    escapeHtml(performanceDayLabel(point, pillar)) + '">' +
    '<span class="performance-bar ' + performanceBarClasses(point, pillar) +
    '" style="height:' + performanceBarHeight(point) + '%"></span>' +
    '</button>'
  ).join("");
  const selected = points.find((point) => point.date === pageState.selectedDay);
  $("#performance-readout").textContent = selected
    ? performanceDayLabel(selected, pillar)
    : "Tap a bar or use the arrows to see a day.";
  const buttons = [...chart.querySelectorAll(".performance-bar-button")];
  const selectedIndex = () => points.findIndex((point) => point.date === pageState.selectedDay);
  const select = (index, focus = false) => {
    const bounded = Math.max(0, Math.min(points.length - 1, index));
    const point = points[bounded];
    pageState.selectedDay = point.date;
    buttons.forEach((item, itemIndex) => {
      item.classList.toggle("is-selected", itemIndex === bounded);
      // One tab stop for the chart; arrow keys move between days.
      item.tabIndex = itemIndex === bounded ? 0 : -1;
    });
    $("#performance-readout").textContent = performanceDayLabel(point, pillar);
    if (focus) buttons[bounded].focus();
    $("#performance-prev").disabled = bounded === 0;
    $("#performance-next").disabled = bounded === points.length - 1;
  };
  buttons.forEach((button, index) => {
    button.tabIndex = index === (selectedIndex() >= 0 ? selectedIndex() : points.length - 1) ? 0 : -1;
    button.addEventListener("click", () => select(index));
    button.addEventListener("mouseenter", () => select(index));
    button.addEventListener("focus", () => select(index));
    button.addEventListener("keydown", (event) => {
      const moves = { ArrowLeft: -1, ArrowRight: 1 };
      if (event.key in moves) {
        event.preventDefault();
        select(index + moves[event.key], true);
      } else if (event.key === "Home" || event.key === "End") {
        event.preventDefault();
        select(event.key === "Home" ? 0 : points.length - 1, true);
      }
    });
  });
  // Larger targets than the bars for touch screens (WCAG 2.5.8).
  $("#performance-prev").onclick = () => {
    const current = selectedIndex();
    select(current < 0 ? points.length - 1 : current - 1);
  };
  $("#performance-next").onclick = () => {
    const current = selectedIndex();
    select(current < 0 ? points.length - 1 : current + 1);
  };
  const current = selectedIndex();
  $("#performance-prev").disabled = current === 0;
  $("#performance-next").disabled = current === points.length - 1;
}

function renderEvents(events) {
  const target = $("#pillar-events");
  const toggle = $("#pillar-events-toggle");
  if (!events.length) {
    target.innerHTML = '<div class="empty-state">No events for this pillar yet.</div>';
    toggle.hidden = true;
    return;
  }
  const visible = pageState.eventsExpanded ? events : events.slice(0, EVENTS_COLLAPSED);
  target.innerHTML = visible.map((event) =>
    '<div class="event-row">' +
    '<div class="event-top">' +
    '<span class="event-type ' + eventTone(event.event_type) + '">' +
    escapeHtml(eventLabel(event.event_type)) + '</span>' +
    renderTime(event.observed_at) +
    '</div>' +
    '<div class="event-detail">' + escapeHtml(eventDescription(event)) + '</div>' +
    '</div>'
  ).join("");
  toggle.hidden = events.length <= EVENTS_COLLAPSED;
  toggle.innerHTML = pageState.eventsExpanded
    ? 'Show less <i class="fa-solid fa-chevron-up" aria-hidden="true"></i>'
    : 'Show all ' + events.length + ' events <i class="fa-solid fa-chevron-down" aria-hidden="true"></i>';
  toggle.setAttribute("aria-expanded", String(pageState.eventsExpanded));
}

function renderHistory(history) {
  $("#pillar-history").innerHTML = history.map((row) =>
    '<tr>' +
    '<td>' + escapeHtml(formatDate(row.observed_at)) + '</td>' +
    '<td><span class="status-badge ' + escapeHtml(row.status) + '">' +
    escapeHtml(statusLabel(row.status)) + '</span></td>' +
    '<td>' + formatNumber(row.produced_momentums) + '</td>' +
    '<td>' + formatNumber(row.expected_momentums) + '</td>' +
    '<td>' + formatWeight(row.weight) + '</td>' +
    '</tr>'
  ).join("") || '<tr><td colspan="5">No snapshots yet.</td></tr>';
}

function renderNotFound(message) {
  setHeaderTitle("Pillar not found");
  $("#pillar-summary").innerHTML =
    '<div class="empty-state">' + escapeHtml(message) +
    ' <a href="/pillars">Browse all pillars</a></div>';
  document.querySelectorAll(".pillar-page > .section-block:not(:first-child)")
    .forEach((section) => { section.hidden = true; });
}

async function loadPillar() {
  if (!ownerAddress) {
    renderNotFound("No pillar address in the link.");
    return;
  }
  try {
    const pillar = await getJson("/api/pillars/" + encodeURIComponent(ownerAddress));
    pageState.pillar = pillar;
    setHeaderTitle(pillar.name);
    renderSummary(pillar);
    renderChart(pillar);
    renderEvents(pillar.events || []);
    renderHistory(pillar.history || []);
  } catch (error) {
    if (!pageState.pillar) renderNotFound("This pillar could not be found.");
    console.error(error);
  }
}

async function shareOrCopy(text, button, copiedLabel) {
  try {
    if (navigator.share && button.classList.contains("share-button")) {
      await navigator.share({ title: document.title, url: text });
      return;
    }
    await navigator.clipboard.writeText(text);
    const original = button.innerHTML;
    button.innerHTML = '<i class="fa-solid fa-check" aria-hidden="true"></i>';
    button.title = copiedLabel;
    window.setTimeout(() => { button.innerHTML = original; }, 1500);
  } catch (error) {
    // Sharing cancelled or clipboard unavailable: nothing to do.
  }
}

document.addEventListener("click", (event) => {
  const copy = event.target.closest("[data-copy]");
  if (copy) {
    void shareOrCopy(copy.dataset.copy, copy, "Address copied");
    return;
  }
  const share = event.target.closest(".share-button");
  if (share) void shareOrCopy(window.location.href, share, "Link copied");
});

$("#pillar-events-toggle").addEventListener("click", () => {
  pageState.eventsExpanded = !pageState.eventsExpanded;
  renderEvents(pageState.pillar?.events || []);
});

void loadPillar();
window.setInterval(loadPillar, 60000);
