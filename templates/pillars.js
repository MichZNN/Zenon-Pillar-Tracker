const state = {
  status: "",
  search: "",
  sort: "rank",
  page: 1,
};
const pageSize = 24;
let performance = {};

function renderDirectoryCard(pillar) {
  return renderPillarCard(
    pillar,
    performance[pillar.owner_address] || pillar.performance_last_30_days || {},
  );
}

function readQuery() {
  const params = new URLSearchParams(window.location.search);
  const status = ["active", "inactive", "favorites"].includes(params.get("status"))
    ? params.get("status")
    : "";
  const sort = PILLAR_SORTS.includes(params.get("sort")) ? params.get("sort") : "rank";
  const rawPage = Number.parseInt(params.get("page") || "1", 10);
  return {
    status,
    search: (params.get("q") || "").trim(),
    sort,
    page: Number.isFinite(rawPage) && rawPage > 0 ? rawPage : 1,
  };
}

function buildUrl(nextState) {
  const params = new URLSearchParams();
  if (nextState.status) params.set("status", nextState.status);
  if (nextState.search) params.set("q", nextState.search);
  if (nextState.sort && nextState.sort !== "rank") params.set("sort", nextState.sort);
  if (nextState.page > 1) params.set("page", String(nextState.page));
  const query = params.toString();
  return "/pillars" + (query ? "?" + query : "");
}

function syncFilterUi() {
  $("#pillar-search").value = state.search;
  $("#sort-filter").value = state.sort;
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

async function loadPillars() {
  Object.assign(state, readQuery());
  syncFilterUi();
  const offset = (state.page - 1) * pageSize;
  const query = new URLSearchParams({
    status: state.status === "favorites" ? "" : state.status,
    q: state.search,
    sort: state.sort,
    limit: String(pageSize),
    offset: String(offset),
    performance: "0",
  });
  if (state.status === "favorites") {
    query.set("owners", [...getFavorites()].join(","));
  }
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
      ? payload.items.map(renderDirectoryCard).join("")
      : '<div class="empty-state">' + (state.status === "favorites" && !getFavorites().size
        ? 'No favorites yet. Tap the <i class="fa-regular fa-star" aria-hidden="true"></i> on a pillar to add it.'
        : 'No pillars found for this filter.') + '</div>';
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

$("#sort-filter").addEventListener("change", (event) => {
  const nextState = { ...state, sort: event.target.value, page: 1 };
  history.pushState({}, "", buildUrl(nextState));
  void loadPillars();
});

// On the favorites tab, removing a star removes the pillar from the list.
window.addEventListener("favoriteschange", () => {
  if (state.status === "favorites") void loadPillars();
});

window.addEventListener("popstate", () => void loadPillars());
void loadPillars();
