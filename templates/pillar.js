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
  const heading = document.querySelector(".site-header h1");
  if (heading) heading.textContent = name;
  document.title = name + " · Zenon Pillar Tracker";
}

function renderSummary(pillar) {
  const performance = pillar.performance_last_30_days || {};
  $("#pillar-rank").textContent = "Rank #" + formatRank(pillar.rank);
  $("#pillar-address").innerHTML =
    '<span class="pillar-address-text">' + escapeHtml(pillar.owner_address) + '</span>' +
    '<button class="icon-button" type="button" data-copy="' +
    escapeHtml(pillar.owner_address) + '" aria-label="Copy address" title="Copy address">' +
    '<i class="fa-regular fa-copy" aria-hidden="true"></i></button>';
  $("#pillar-actions").innerHTML =
    '<span class="status-badge ' + escapeHtml(pillar.status) + '">' +
    escapeHtml(statusLabel(pillar.status)) + '</span>' +
    favoriteButton(pillar) +
    '<button class="icon-button share-button" type="button" aria-label="Share this pillar" title="Share this pillar">' +
    '<i class="fa-solid fa-share-nodes" aria-hidden="true"></i></button>';
  $("#pillar-summary").innerHTML =
    '<div class="pillar-stats pillar-page-stats">' +
    '<div class="stat-item"><span class="stat-label">Performance (30 days)</span><strong class="performance-value' +
    (performanceAvailable(performance) ? '' : ' empty') + '">' +
    formatPerformance(performance) + '</strong></div>' +
    '<div class="stat-item"><span class="stat-label">Produced / expected · this epoch</span><strong>' +
    formatNumber(pillar.produced_momentums ?? 0) + ' / ' +
    formatNumber(pillar.expected_momentums ?? 0) + '</strong></div>' +
    '<div class="stat-item"><span class="stat-label">Weight</span><strong>' +
    formatWeight(pillar.weight) + '</strong></div>' +
    '<div class="stat-item"><span class="stat-label">' +
    statusDurationLabel(pillar.status) + '</span><strong>' +
    formatDuration(statusDurationSeconds(pillar)) + '</strong></div>' +
    '<div class="stat-item"><span class="stat-label">Momentum share</span><strong>' +
    escapeHtml(pillar.momentum_reward_percentage) + '%</strong></div>' +
    '<div class="stat-item"><span class="stat-label">Delegate share</span><strong>' +
    escapeHtml(pillar.delegate_reward_percentage) + '%</strong></div>' +
    '</div>';
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
    : "Tap a bar to see that day.";
  chart.querySelectorAll(".performance-bar-button").forEach((button) => {
    const point = points[Number(button.dataset.index)];
    const select = () => {
      pageState.selectedDay = point.date;
      chart.querySelectorAll(".is-selected").forEach((item) => item.classList.remove("is-selected"));
      button.classList.add("is-selected");
      $("#performance-readout").textContent = performanceDayLabel(point, pillar);
    };
    button.addEventListener("click", select);
    button.addEventListener("mouseenter", select);
    button.addEventListener("focus", select);
  });
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
