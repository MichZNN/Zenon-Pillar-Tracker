let csrfToken = "";
let ownSubscriptions = [];
let adminUsers = [];
let adminSubscriptions = [];
let operationalLogEntries = [];
let logRefreshPromise = null;
let logRefreshTimer = null;
let lastLogUpdatedAt = null;

const LOG_FETCH_LIMIT = 60;
const LOG_PREVIEW_LIMIT = 12;
const LOG_REFRESH_INTERVAL_MS = 10000;

const DEFAULT_SUBSCRIPTION_EVENTS = [
  "pillar_inactive",
  "pillar_active",
  "reward_shares_changed",
];

const EVENT_LABELS = {
  pillar_created: "Pillar created",
  pillar_dismantled: "Pillar dismantled",
  pillar_name_changed: "Pillar name changed",
  reward_shares_changed: "Reward shares changed",
  pillar_inactive: "Pillar inactive",
  pillar_active: "Pillar active again",
  epoch_available: "Epoch rewards available",
};

function $(selector) { return document.querySelector(selector); }

function updateModalBodyLock() {
  const accountModal = $("#account-modal");
  const logsModal = $("#logs-modal");
  document.body.classList.toggle(
    "modal-open",
    Boolean(
      (accountModal && !accountModal.hidden) ||
      (logsModal && !logsModal.hidden)
    ),
  );
}

function initialiseUserMenu() {
  const menu = $("#user-menu");
  const toggle = $("#user-menu-toggle");
  const dropdown = $("#user-menu-dropdown");
  if (!menu || !toggle || !dropdown) return;

  const setOpen = (open) => {
    menu.classList.toggle("is-open", open);
    dropdown.hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
  };

  toggle.addEventListener("click", () => setOpen(!menu.classList.contains("is-open")));
  dropdown.querySelectorAll("a, button").forEach((item) => {
    item.addEventListener("click", () => setOpen(false));
  });
  document.addEventListener("click", (event) => {
    if (!menu.contains(event.target)) setOpen(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && menu.classList.contains("is-open")) {
      setOpen(false);
      toggle.focus();
    }
  });
}

function setAccountModalOpen(open) {
  const modal = $("#account-modal");
  const close = $("#close-account-modal");
  const menuToggle = $("#user-menu-toggle");
  const accountForm = $("#account-form");
  if (!modal) return;
  if (accountForm) {
    accountForm.elements.current_password.value = "";
    accountForm.elements.new_password.value = "";
    accountForm.elements.new_password_confirmation.value = "";
  }
  modal.hidden = !open;
  updateModalBodyLock();
  if (open) close?.focus();
  else menuToggle?.focus();
}

function initialiseAccountModal() {
  const modal = $("#account-modal");
  const accountLink = $("#account-menu-link");
  const close = $("#close-account-modal");
  if (!modal || !accountLink || !close) return;

  accountLink.addEventListener("click", (event) => {
    event.preventDefault();
    setAccountModalOpen(true);
  });
  close.addEventListener("click", () => setAccountModalOpen(false));
  modal.addEventListener("click", (event) => {
    if (event.target === modal) setAccountModalOpen(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !modal.hidden) setAccountModalOpen(false);
  });
}

function escapeHtml(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}

async function getJson(url, options = {}) {
  const response = await fetch(url, { cache: "no-store", ...options });
  const payload = await response.json();
  if (!response.ok) {
    const error = new Error(payload.error || "Request failed");
    error.payload = payload;
    throw error;
  }
  return payload;
}

function listValue(value) {
  return String(value || "").split(/[\n,]/).map((item) => item.trim()).filter(Boolean);
}

function selectedEvents(form) {
  return Array.from(form.querySelectorAll('input[name="events"]:checked'))
    .map((input) => input.value);
}

function setSelectedEvents(form, events = DEFAULT_SUBSCRIPTION_EVENTS) {
  const selected = new Set(events || []);
  form.querySelectorAll('input[name="events"]').forEach((input) => {
    input.checked = selected.has(input.value);
  });
}

function displayEvents(events) {
  return (Array.isArray(events) ? events : []).map((event) => EVENT_LABELS[event] || event).join(", ");
}

function destinationTypes(item) {
  const destinations = [];
  if (item.channel_id) destinations.push("Telegram");
  if (item.discord_webhook) destinations.push("Discord");
  return destinations.join(" + ") || "No destination";
}

function destinationTooltip(item) {
  const destinations = [];
  if (item.channel_id) destinations.push(`Telegram channel: ${item.channel_id}`);
  if (item.discord_webhook) destinations.push("Discord webhook configured");
  return destinations.join(" · ") || "No notification destination configured";
}

function subscriptionDestinations(item) {
  return destinationTooltip(item);
}

function pillarSummary(item) {
  const count = Array.isArray(item.pillar_owner_addresses)
    ? item.pillar_owner_addresses.length : 0;
  return count ? `${count} pillar${count === 1 ? "" : "s"}` : "All pillars";
}

function pillarTooltip(item) {
  const addresses = Array.isArray(item.pillar_owner_addresses)
    ? item.pillar_owner_addresses.filter(Boolean) : [];
  return addresses.length ? addresses.join("\n") : "Notifications for all pillars";
}

function eventSummary(item) {
  const count = Array.isArray(item.events) ? item.events.length : 0;
  return `${count} event${count === 1 ? "" : "s"}`;
}

function eventTooltip(item) {
  return displayEvents(item.events) || "No events selected";
}

function subscriptionInfo(summary, tooltip, className = "", label = "") {
  const dataLabel = label === "Events"
    ? 'data-label="Events"'
    : `data-label="${escapeHtml(label)}"`;
  return `<span class="table-info subscription-summary-item ${className}" tabindex="0" title="${escapeHtml(tooltip)}" data-tooltip="${escapeHtml(tooltip)}" ${dataLabel}><span class="subscription-summary-label">${escapeHtml(label)}</span><span class="subscription-summary-value">${escapeHtml(summary)}</span><i class="fa-solid fa-circle-info" aria-hidden="true"></i></span>`;
}

function subscriptionDestinationCell(item) {
  const label = item.label || destinationTypes(item);
  const types = item.label ? destinationTypes(item) : "";
  return `<div class="table-info subscription-destination" tabindex="0" title="${escapeHtml(destinationTooltip(item))}" data-tooltip="${escapeHtml(destinationTooltip(item))}"><strong>${escapeHtml(label)}</strong>${types ? `<small>${escapeHtml(types)}</small>` : ""}</div>`;
}

function subscriptionDetails(item) {
  const addresses = Array.isArray(item.pillar_owner_addresses)
    ? item.pillar_owner_addresses.filter(Boolean) : [];
  const events = Array.isArray(item.events) ? item.events : [];
  const addressMarkup = addresses.length
    ? `<ul class="subscription-detail-list">${addresses.map((address) => `<li>${escapeHtml(address)}</li>`).join("")}</ul>`
    : '<span class="subscription-detail-value">All pillars</span>';
  const eventMarkup = events.length
    ? `<ul class="subscription-detail-list">${events.map((event) => `<li>${escapeHtml(EVENT_LABELS[event] || event)}</li>`).join("")}</ul>`
    : '<span class="subscription-detail-value">No events selected</span>';
  return '<details class="subscription-details">' +
    '<summary>View details</summary>' +
    '<div class="subscription-detail-grid">' +
    '<div class="subscription-detail-block"><span class="subscription-detail-label">Destinations</span><span class="subscription-detail-value">' +
    escapeHtml(destinationTooltip(item)) + '</span></div>' +
    '<div class="subscription-detail-block"><span class="subscription-detail-label">Pillars</span>' +
    addressMarkup + '</div>' +
    '<div class="subscription-detail-block"><span class="subscription-detail-label">Events</span>' +
    eventMarkup + '</div>' +
    '</div></details>';
}

function subscriptionCard(item, editAttribute, owner = "") {
  const status = item.active ? "active" : "inactive";
  return '<article class="subscription-card">' +
    '<div class="subscription-card-header"><div class="subscription-card-title">' +
    subscriptionDestinationCell(item) + '</div><span class="status-badge ' + status + '">' +
    (item.active ? "Active" : "Inactive") + '</span></div>' +
    (owner ? '<div class="subscription-card-owner">Owner: ' + escapeHtml(owner) + '</div>' : '') +
    '<div class="subscription-summary" aria-label="Subscription summary">' +
    subscriptionInfo(pillarSummary(item), pillarTooltip(item), "", "Pillars") +
    subscriptionInfo(eventSummary(item), eventTooltip(item), "event-count", "Events") +
    '</div>' +
    subscriptionDetails(item) +
    '<div class="subscription-card-actions"><button class="ghost-button small-button" ' +
    editAttribute + ' type="button">Edit</button></div>' +
    '</article>';
}

function requireSubscriptionDestination(form) {
  if (!form.elements.channel_id.value.trim() && !form.elements.discord_webhook.value.trim()) {
    throw new Error("Enter a Telegram channel ID, a Discord webhook, or both.");
  }
}

function settingsForms() {
  return Array.from(document.querySelectorAll("[data-settings-form]"));
}

function populateSettingsForm(settings) {
  settingsForms().forEach((form) => {
    Array.from(form.elements).forEach((control) => {
      if (!control.name || settings[control.name] === undefined) return;
      const value = settings[control.name];
      if (control.type === "checkbox") {
        control.checked = Boolean(value);
      } else if (control.dataset.settingType === "list") {
        control.value = Array.isArray(value) ? value.join("\n") : String(value || "");
      } else {
        control.value = value ?? "";
      }
    });
  });
}

function readSettingsForm(form) {
  const settings = {};
  Array.from(form.elements).forEach((control) => {
    if (!control.name) return;
    if (control.type === "checkbox") {
      settings[control.name] = control.checked;
    } else if (control.dataset.settingType === "list") {
      settings[control.name] = listValue(control.value);
    } else if (control.type === "number") {
      if (!control.value.trim()) throw new Error(`${control.name} is required.`);
      const value = Number(control.value);
      if (!Number.isFinite(value)) throw new Error(`${control.name} must be a number.`);
      settings[control.name] = value;
    } else {
      settings[control.name] = control.value;
    }
  });
  return settings;
}

function initialiseAdminNavigation() {
  const links = Array.from(document.querySelectorAll("[data-admin-nav]"));
  const sections = links
    .map((link) => document.getElementById(link.hash.slice(1)))
    .filter(Boolean);
  if (!links.length || !sections.length) return;

  const menu = document.querySelector(".admin-menu");
  const toggle = $("#admin-menu-toggle");
  const toggleLabel = $("#admin-menu-toggle-label");

  const setMenuOpen = (open) => {
    if (!menu || !toggle) return;
    menu.classList.toggle("is-open", open);
    toggle.setAttribute("aria-expanded", String(open));
  };

  const activate = (id) => {
    links.forEach((link) => {
      const active = link.hash.slice(1) === id;
      link.classList.toggle("is-active", active);
      if (active) link.setAttribute("aria-current", "location");
      else link.removeAttribute("aria-current");
    });
    const activeLink = links.find((link) => link.hash.slice(1) === id);
    if (toggleLabel && activeLink) {
      toggleLabel.textContent = activeLink.querySelector("span")?.textContent || "Sections";
    }
  };

  links.forEach((link) => {
    link.addEventListener("click", () => {
      activate(link.hash.slice(1));
      setMenuOpen(false);
    });
  });

  toggle?.addEventListener("click", () => {
    setMenuOpen(!menu?.classList.contains("is-open"));
  });
  document.addEventListener("click", (event) => {
    if (menu && !menu.contains(event.target)) setMenuOpen(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") setMenuOpen(false);
  });

  const initialSection = window.location.hash.slice(1);
  if (sections.some((section) => section.id === initialSection)) {
    activate(initialSection);
  } else {
    activate(links[0].hash.slice(1));
  }

  if (!("IntersectionObserver" in window)) return;
  const observer = new IntersectionObserver((entries) => {
    const visible = entries
      .filter((entry) => entry.isIntersecting)
      .sort((left, right) => left.boundingClientRect.top - right.boundingClientRect.top);
    if (visible[0]) activate(visible[0].target.id);
  }, { rootMargin: "-112px 0px -55% 0px", threshold: 0 });
  sections.forEach((section) => observer.observe(section));
}

const TOAST_DURATION_MS = 10000;
const TOAST_CLOSE_DURATION_MS = 220;

function showToast(value, error = false) {
  const container = $("#toast-container");
  if (!container || !value) return;

  const toast = document.createElement("div");
  toast.className = "toast" + (error ? " error" : "");
  toast.setAttribute("role", error ? "alert" : "status");

  const content = document.createElement("span");
  content.className = "toast-content";
  content.textContent = value;

  const close = document.createElement("button");
  close.className = "toast-close";
  close.type = "button";
  close.setAttribute("aria-label", "Dismiss notification");
  close.textContent = "×";

  const progress = document.createElement("span");
  progress.className = "toast-progress";

  toast.append(content, close, progress);
  container.append(toast);
  let timer;
  let dismissed = false;

  const dismiss = () => {
    if (dismissed) return;
    dismissed = true;
    window.clearTimeout(timer);
    toast.classList.add("is-closing");
    window.setTimeout(() => toast.remove(), TOAST_CLOSE_DURATION_MS);
  };

  close.addEventListener("click", dismiss);
  window.requestAnimationFrame(() => progress.classList.add("is-running"));
  timer = window.setTimeout(dismiss, TOAST_DURATION_MS);
}

function showMessage(value, error = false) {
  const target = $("#portal-message");
  target.textContent = value || "";
  target.className = "sr-only";
  showToast(value, error);
}

function installValidationNotifications() {
  document.querySelectorAll("form").forEach((form) => {
    form.addEventListener("invalid", (event) => {
      if (form.dataset.validationNotified) return;
      form.dataset.validationNotified = "true";
      showToast(event.target.validationMessage || "Please check the form.", true);
      window.setTimeout(() => delete form.dataset.validationNotified, 0);
    }, true);
  });
}

function resetOwnSubscriptionForm() {
  const form = $("#subscription-form");
  form.reset();
  form.elements.id.value = "";
  setSelectedEvents(form);
  form.elements.active.checked = true;
  $("#cancel-edit").hidden = true;
}

function renderOwnSubscriptions() {
  const target = $("#subscription-list");
  if (!ownSubscriptions.length) {
    target.innerHTML = '<div class="empty-state">No subscriptions yet.</div>';
    return;
  }
  target.innerHTML = ownSubscriptions.map((item) =>
    subscriptionCard(item, `data-edit-own="${item.id}"`)
  ).join("");
  target.querySelectorAll("[data-edit-own]").forEach((button) => {
    button.addEventListener("click", () => editOwnSubscription(Number(button.dataset.editOwn)));
  });
}

function editOwnSubscription(id) {
  const item = ownSubscriptions.find((entry) => Number(entry.id) === id);
  if (!item) return;
  const form = $("#subscription-form");
  form.elements.id.value = item.id;
  form.elements.label.value = item.label || "";
  form.elements.channel_id.value = item.channel_id || "";
  form.elements.discord_webhook.value = item.discord_webhook || "";
  form.elements.pillar_owner_addresses.value = (item.pillar_owner_addresses || []).join("\n");
  setSelectedEvents(form, item.events);
  form.elements.active.checked = Boolean(item.active);
  $("#cancel-edit").hidden = false;
  form.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function loadOwnSubscriptions() {
  ownSubscriptions = await getJson("/api/subscriptions");
  renderOwnSubscriptions();
}

async function saveAccount(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const newPassword = form.elements.new_password.value;
  const confirmation = form.elements.new_password_confirmation.value;
  if (newPassword !== confirmation) {
    showMessage("New passwords do not match.", true);
    return;
  }
  if ((newPassword || confirmation) && !form.elements.current_password.value) {
    showMessage("Enter your current password to change your password.", true);
    return;
  }
  try {
    const updated = await getJson("/api/account", {
      method: "PATCH",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
      body: JSON.stringify({
        display_name: form.elements.display_name.value,
        current_password: form.elements.current_password.value,
        new_password: newPassword,
        new_password_confirmation: confirmation,
      }),
    });
    form.elements.display_name.value = updated.display_name || "";
    form.elements.current_password.value = "";
    form.elements.new_password.value = "";
    form.elements.new_password_confirmation.value = "";
    $("#user-name").textContent = updated.display_name || updated.username;
    showMessage("Account saved.");
  } catch (error) {
    showMessage(error.message, true);
  }
}

async function saveOwnSubscription(event) {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    requireSubscriptionDestination(form);
    const body = {
      label: form.elements.label.value,
      channel_id: form.elements.channel_id.value,
      discord_webhook: form.elements.discord_webhook.value,
      pillar_owner_addresses: listValue(form.elements.pillar_owner_addresses.value),
      events: selectedEvents(form),
      active: form.elements.active.checked,
    };
    const id = form.elements.id.value;
    await getJson(id ? `/api/subscriptions/${id}` : "/api/subscriptions", {
      method: id ? "PATCH" : "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
      body: JSON.stringify(body),
    });
    showMessage("Subscription saved.");
    resetOwnSubscriptionForm();
    await loadOwnSubscriptions();
  } catch (error) {
    showMessage(error.message, true);
  }
}

function resetUserForm() {
  const form = $("#user-form");
  form.reset();
  form.elements.id.value = "";
  form.elements.username.readOnly = false;
  form.elements.password.required = true;
  form.elements.active.checked = true;
  $("#cancel-user").hidden = true;
}

function renderUsers() {
  $("#user-list").innerHTML = adminUsers.length ? adminUsers.map((item) =>
    `<tr><td><strong>${escapeHtml(item.username)}</strong>${item.display_name ? `<small>${escapeHtml(item.display_name)}</small>` : ""}</td>` +
    `<td>${escapeHtml(item.role)}</td><td><span class="status-badge ${item.active ? "active" : "inactive"}">${item.active ? "Active" : "Inactive"}</span></td>` +
    `<td>${item.subscription_count || 0}</td><td>${escapeHtml(item.last_login_at || "—")}</td>` +
    `<td><button class="ghost-button small-button" data-edit-user="${item.id}" type="button">Edit</button></td></tr>`
  ).join("") : '<tr><td colspan="6" class="empty-state">No users.</td></tr>';
  $("#owner-user").innerHTML = '<option value="">Unassigned</option>' + adminUsers.map((item) =>
    `<option value="${item.id}">${escapeHtml(item.username)}${item.active ? "" : " (inactive)"}</option>`
  ).join("");
  $("#user-list").querySelectorAll("[data-edit-user]").forEach((button) => {
    button.addEventListener("click", () => editUser(Number(button.dataset.editUser)));
  });
}

function editUser(id) {
  const item = adminUsers.find((entry) => Number(entry.id) === id);
  if (!item) return;
  const form = $("#user-form");
  form.elements.id.value = item.id;
  form.elements.username.value = item.username;
  form.elements.username.readOnly = true;
  form.elements.display_name.value = item.display_name || "";
  form.elements.password.value = "";
  form.elements.password.required = false;
  form.elements.role.value = item.role;
  form.elements.active.checked = Boolean(item.active);
  $("#cancel-user").hidden = false;
  form.scrollIntoView({ behavior: "smooth", block: "start" });
}

function resetAdminSubscriptionForm() {
  const form = $("#admin-subscription-form");
  form.reset();
  form.elements.id.value = "";
  form.elements.owner_user_id.value = "";
  setSelectedEvents(form);
  form.elements.active.checked = true;
  $("#cancel-subscription").hidden = true;
}

function renderAdminSubscriptions() {
  $("#admin-subscription-list").innerHTML = adminSubscriptions.length ? adminSubscriptions.map((item) =>
    subscriptionCard(
      item,
      `data-edit-subscription="${item.id}"`,
      item.owner_username || "Unassigned",
    )
  ).join("") : '<div class="empty-state">No subscriptions.</div>';
  $("#admin-subscription-list").querySelectorAll("[data-edit-subscription]").forEach((button) => {
    button.addEventListener("click", () => editAdminSubscription(Number(button.dataset.editSubscription)));
  });
}

function editAdminSubscription(id) {
  const item = adminSubscriptions.find((entry) => Number(entry.id) === id);
  if (!item) return;
  const form = $("#admin-subscription-form");
  form.elements.id.value = item.id;
  form.elements.owner_user_id.value = item.user_id ?? "";
  form.elements.label.value = item.label || "";
  form.elements.channel_id.value = item.channel_id || "";
  form.elements.discord_webhook.value = item.discord_webhook || "";
  form.elements.pillar_owner_addresses.value = (item.pillar_owner_addresses || []).join("\n");
  setSelectedEvents(form, item.events);
  form.elements.active.checked = Boolean(item.active);
  $("#cancel-subscription").hidden = false;
  form.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function loadSettings() {
  const settings = await getJson("/api/admin/settings");
  populateSettingsForm(settings);
}

function formatDiagnosticDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

function renderCollectorDiagnostics(diagnostics) {
  const badge = $("#collector-data-status");
  const message = $("#collector-data-message");
  const attempt = $("#collector-last-attempt");
  const success = $("#collector-last-success");
  const error = $("#collector-last-error");
  if (!badge || !message || !attempt || !success || !error) return;

  const status = diagnostics || {};
  const state = String(status.state || "unknown").toLowerCase();
  const badgeClass = state === "green"
    ? "active"
    : state === "red" ? "error"
      : state === "orange" ? "warning" : "inactive";
  badge.className = `status-badge ${badgeClass}`;
  badge.textContent = status.label || "Waiting for tracker";
  message.textContent = status.description || "Collector diagnostics are not available yet.";
  attempt.textContent = status.last_attempt_at
    ? `Last attempt: ${formatDiagnosticDate(status.last_attempt_at)}${status.last_attempt_status ? ` (${status.last_attempt_status})` : ""}`
    : "No collector attempt recorded yet.";
  success.textContent = status.last_success_at
    ? `Last successful poll: ${formatDiagnosticDate(status.last_success_at)}`
    : "No successful poll recorded yet.";
  error.hidden = !status.last_error;
  error.textContent = status.last_error ? `Latest error: ${status.last_error}` : "";
}

function logSourceLabel(source) {
  return {
    application: "Application",
    collector: "Collector",
    audit: "Audit",
  }[source] || "Log";
}

function logLevel(value) {
  const match = String(value || "").match(/\b(CRITICAL|ERROR|WARNING|WARN|INFO|DEBUG)\b/i);
  if (!match) return "LOG";
  return match[1].toUpperCase() === "WARN" ? "WARNING" : match[1].toUpperCase();
}

function logTimestamp(value) {
  const match = String(value || "").match(
    /\b20\d{2}-\d{2}-\d{2}[T ][0-9]{2}:[0-9]{2}:[0-9]{2}(?:[.,][0-9]+)?(?:Z|[+-][0-9]{2}:?[0-9]{2})?\b/
  );
  return match ? match[0] : "";
}

function createLogEntry(source, text, options = {}) {
  const value = String(text ?? "").trim();
  return {
    source,
    text: value,
    timestamp: options.timestamp || logTimestamp(value),
    level: options.level || logLevel(value),
    order: Number(options.order) || 0,
  };
}

function parseApplicationLogs(lines) {
  return (Array.isArray(lines) ? lines : [])
    .map((line, order) => createLogEntry("application", line, { order }))
    .filter((entry) => entry.text);
}

function parseCollectorLogs(logs) {
  return String(logs || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .reverse()
    .map((line, order) => createLogEntry("collector", line, { order }));
}

function auditDetails(item) {
  const details = item.details && Object.keys(item.details).length
    ? ` · ${JSON.stringify(item.details)}`
    : "";
  const entity = `${item.entity_type || "record"}${item.entity_id ? ` #${item.entity_id}` : ""}`;
  return `${item.username || "system"} · ${item.action || "action"} · ${entity}${details}`;
}

function parseAuditLogs(items) {
  return (Array.isArray(items) ? items : [])
    .map((item, order) => createLogEntry("audit", auditDetails(item), {
      timestamp: item.created_at,
      level: "AUDIT",
      order,
    }))
    .filter((entry) => entry.text);
}

function sortLogEntries(entries) {
  return entries.slice().sort((left, right) => {
    const leftTime = Date.parse(left.timestamp || "");
    const rightTime = Date.parse(right.timestamp || "");
    if (Number.isFinite(leftTime) && Number.isFinite(rightTime) && leftTime !== rightTime) {
      return rightTime - leftTime;
    }
    return left.order - right.order;
  });
}

function replaceLogSource(source, entries) {
  operationalLogEntries = sortLogEntries([
    ...operationalLogEntries.filter((entry) => entry.source !== source),
    ...entries,
  ]);
  renderLogViews();
}

function logFilterState() {
  const rawLimit = Number.parseInt($("#log-line-limit")?.value || "50", 10);
  return {
    source: $("#log-source-filter")?.value || "all",
    level: $("#log-level-filter")?.value || "all",
    search: ($("#log-search")?.value || "").trim().toLowerCase(),
    limit: Number.isFinite(rawLimit) ? Math.max(1, rawLimit) : 50,
  };
}

function logMatches(entry, filters) {
  if (filters.source !== "all" && entry.source !== filters.source) return false;
  if (filters.level !== "all") {
    const acceptedLevels = filters.level === "ERROR"
      ? ["ERROR", "CRITICAL"]
      : [filters.level];
    if (!acceptedLevels.includes(entry.level)) return false;
  }
  if (filters.search && !formatLogEntry(entry).toLowerCase().includes(filters.search)) {
    return false;
  }
  return true;
}

function formatLogEntry(entry) {
  const hasTimestamp = entry.timestamp && entry.text.includes(entry.timestamp);
  const timestamp = entry.timestamp && !hasTimestamp ? ` ${entry.timestamp}` : "";
  const level = entry.level && entry.level !== "LOG" ? ` ${entry.level}` : "";
  return `[${logSourceLabel(entry.source)}]${timestamp}${level} ${entry.text}`.trim();
}

function setLogViewerContent(viewer, entries, emptyMessage) {
  if (!viewer) return;
  viewer.textContent = entries.length
    ? entries.map(formatLogEntry).join("\n")
    : emptyMessage;
}

function renderLogViews() {
  const filters = logFilterState();
  const matchingEntries = operationalLogEntries.filter((entry) => logMatches(entry, filters));
  const previewEntries = operationalLogEntries.slice(0, LOG_PREVIEW_LIMIT);
  const visibleEntries = matchingEntries.slice(0, filters.limit);
  setLogViewerContent(
    $("#collector-container-log"),
    previewEntries,
    operationalLogEntries.length ? "No log entries yet." : "No log entries available.",
  );
  setLogViewerContent(
    $("#logs-modal-viewer"),
    visibleEntries,
    operationalLogEntries.length ? "No entries match these filters." : "No log entries available.",
  );
  const previewCount = $("#logs-preview-count");
  if (previewCount) {
    previewCount.textContent = previewEntries.length
      ? `${previewEntries.length} latest entries`
      : "No entries";
  }
  const status = $("#logs-modal-status");
  if (status) {
    const updated = lastLogUpdatedAt
      ? ` · Updated ${formatDiagnosticDate(lastLogUpdatedAt.toISOString())}`
      : "";
    status.textContent = `${visibleEntries.length} of ${matchingEntries.length} matching entries${updated}`;
  }
}

async function loadLogs() {
  try {
    const payload = await getJson(`/api/admin/logs?limit=${LOG_FETCH_LIMIT}&lines=${LOG_FETCH_LIMIT}`);
    renderCollectorDiagnostics(payload.collector);
    const file = payload.file || {};
    $("#log-file-info").textContent = `${file.path || "data_store/pillar_tracker.log"} · ${file.exists ? (file.size_bytes || 0) + " bytes" : "not created"} · latest ${LOG_FETCH_LIMIT}`;
    replaceLogSource("application", parseApplicationLogs(file.lines));
    replaceLogSource("audit", parseAuditLogs(payload.audit));
  } catch (error) {
    $("#log-file-info").textContent = "Application log unavailable";
    replaceLogSource("application", [createLogEntry(
      "application",
      `Could not load application logs: ${error.message}`,
      { level: "ERROR", order: 0 },
    )]);
    renderCollectorDiagnostics(error.payload?.diagnostics);
  }
}

function setCollectorControlButtons(available, running, busy = false) {
  const start = $("#collector-start");
  const stop = $("#collector-stop");
  const restart = $("#collector-restart");
  if (start) start.disabled = busy || !available || running;
  if (stop) stop.disabled = busy || !available || !running;
  if (restart) restart.disabled = busy || !available;
}

function renderCollectorControl(payload) {
  const badge = $("#collector-process-status");
  const message = $("#collector-control-message");
  if (!badge || !message) return;
  renderCollectorDiagnostics(payload.diagnostics);
  const collector = payload.collector || {};
  const available = payload.available !== false;
  const running = Boolean(collector.running);
  const state = String(collector.state || "").toLowerCase();
  badge.className = `status-badge ${!available ? "error" : running ? "active" : "inactive"}`;
  badge.textContent = !available
    ? "Unavailable"
    : running
      ? "Running"
      : state === "not_created" ? "Not created" : "Stopped";
  message.textContent = payload.error || collector.status || (
    running ? "The collector is running." : "The collector is not running."
  );
  setCollectorControlButtons(available, running);
}

async function loadCollectorControl() {
  try {
    renderCollectorControl(await getJson("/api/admin/collector-control"));
  } catch (error) {
    renderCollectorControl({
      ...(error.payload || {}),
      available: false,
      error: error.message,
    });
  }
}

async function loadCollectorLogs() {
  const info = $("#collector-log-info");
  const viewer = $("#collector-container-log");
  if (!info || !viewer) return;
  try {
    const payload = await getJson(`/api/admin/collector-logs?tail=${LOG_FETCH_LIMIT}`);
    renderCollectorControl(payload);
    const collector = payload.collector || {};
    info.textContent = collector.name
      ? `${collector.name} · ${collector.status || collector.state || ""}`
      : "Collector container logs";
    replaceLogSource("collector", parseCollectorLogs(payload.logs));
  } catch (error) {
    info.textContent = "Collector container logs";
    replaceLogSource("collector", [createLogEntry(
      "collector",
      `Could not load collector logs: ${error.message}`,
      { level: "ERROR", order: 0 },
    )]);
    renderCollectorControl({
      ...(error.payload || {}),
      available: error.payload ? error.payload.available !== false : false,
      error: error.message,
    });
    renderCollectorDiagnostics(error.payload?.diagnostics);
  }
}

async function controlCollector(action) {
  setCollectorControlButtons(false, false, true);
  const message = $("#collector-control-message");
  if (message) message.textContent = `${action[0].toUpperCase()}${action.slice(1)} command is being sent…`;
  try {
    const payload = await getJson("/api/admin/collector-control", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
      body: JSON.stringify({ action }),
    });
    renderCollectorControl(payload);
    const running = Boolean((payload.collector || {}).running);
    showMessage(
      running || action === "stop"
        ? `Collector ${action} command completed.`
        : `Collector ${action} command completed, but it is not running.`,
      !running && action !== "stop",
    );
    await refreshOperations();
  } catch (error) {
    showMessage(error.message, true);
    await loadCollectorControl();
    await loadCollectorLogs();
  }
}

async function refreshOperations() {
  if (logRefreshPromise) return logRefreshPromise;
  setLogRefreshBusy(true);
  logRefreshPromise = Promise.all([
    loadLogs(),
    loadCollectorControl(),
    loadCollectorLogs(),
  ]).then(() => {
    lastLogUpdatedAt = new Date();
    renderLogViews();
  }).finally(() => {
    setLogRefreshBusy(false);
    logRefreshPromise = null;
  });
  return logRefreshPromise;
}

function setLogRefreshBusy(busy) {
  document.querySelectorAll("#refresh-logs, #refresh-modal-logs").forEach((button) => {
    button.disabled = busy;
    button.classList.toggle("is-loading", busy);
  });
}

function startLogAutoRefresh() {
  if (logRefreshTimer) window.clearInterval(logRefreshTimer);
  logRefreshTimer = window.setInterval(() => void refreshOperations(), LOG_REFRESH_INTERVAL_MS);
}

function setLogsModalOpen(open) {
  const modal = $("#logs-modal");
  if (!modal) return;
  modal.hidden = !open;
  updateModalBodyLock();
  if (open) {
    renderLogViews();
    void refreshOperations();
    $("#log-source-filter")?.focus();
  } else {
    $("#open-logs-modal")?.focus();
  }
}

function initialiseLogsModal() {
  const modal = $("#logs-modal");
  const open = $("#open-logs-modal");
  const close = $("#close-logs-modal");
  if (!modal || !open || !close) return;
  open.addEventListener("click", () => setLogsModalOpen(true));
  close.addEventListener("click", () => setLogsModalOpen(false));
  $("#refresh-modal-logs")?.addEventListener("click", () => void refreshOperations());
  modal.addEventListener("click", (event) => {
    if (event.target === modal) setLogsModalOpen(false);
  });
  ["#log-source-filter", "#log-level-filter", "#log-line-limit"].forEach((selector) => {
    $(selector)?.addEventListener("change", renderLogViews);
  });
  $("#log-search")?.addEventListener("input", renderLogViews);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !modal.hidden) setLogsModalOpen(false);
  });
}

async function saveSettings(event) {
  event.preventDefault();
  const form = event.currentTarget;
  let settings;
  try {
    settings = readSettingsForm(form);
  } catch (error) {
    showMessage(error.message, true);
    return;
  }
  try {
    await getJson("/api/admin/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
      body: JSON.stringify({ settings }),
    });
    showMessage(`${form.dataset.settingsLabel || "Settings"} saved.`);
    await loadSettings();
  } catch (error) {
    showMessage(error.message, true);
  }
}

async function saveUser(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const id = form.elements.id.value;
  const body = {
    display_name: form.elements.display_name.value,
    role: form.elements.role.value,
    active: form.elements.active.checked,
  };
  if (!id) {
    body.username = form.elements.username.value;
    body.password = form.elements.password.value;
  } else if (form.elements.password.value) {
    body.password = form.elements.password.value;
  }
  try {
    await getJson(id ? `/api/admin/users/${id}` : "/api/admin/users", {
      method: id ? "PATCH" : "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
      body: JSON.stringify(body),
    });
    showMessage("User saved.");
    resetUserForm();
    adminUsers = await getJson("/api/admin/users");
    renderUsers();
  } catch (error) {
    showMessage(error.message, true);
  }
}

async function saveAdminSubscription(event) {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    requireSubscriptionDestination(form);
    const id = form.elements.id.value;
    const body = {
      owner_user_id: form.elements.owner_user_id.value || null,
      label: form.elements.label.value,
      channel_id: form.elements.channel_id.value,
      discord_webhook: form.elements.discord_webhook.value,
      pillar_owner_addresses: listValue(form.elements.pillar_owner_addresses.value),
      events: selectedEvents(form),
      active: form.elements.active.checked,
    };
    await getJson(id ? `/api/admin/subscriptions/${id}` : "/api/admin/subscriptions", {
      method: id ? "PATCH" : "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
      body: JSON.stringify(body),
    });
    showMessage("Subscription saved.");
    resetAdminSubscriptionForm();
    adminSubscriptions = await getJson("/api/admin/subscriptions");
    renderAdminSubscriptions();
    adminUsers = await getJson("/api/admin/users");
    renderUsers();
  } catch (error) {
    showMessage(error.message, true);
  }
}

async function logout() {
  if (logRefreshTimer) {
    window.clearInterval(logRefreshTimer);
    logRefreshTimer = null;
  }
  try {
    await getJson("/api/auth/logout", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
    });
  } finally {
    location.replace("/");
  }
}

async function initialise() {
  const me = await getJson("/api/auth/me");
  if (!me.authenticated) {
    location.replace("/login?next=/portal");
    return;
  }
  csrfToken = me.csrf_token || "";
  $("#user-name").textContent = me.user.display_name || me.user.username;
  if (me.user.role !== "admin") {
    const accountMenuLink = $("#account-menu-link");
    accountMenuLink.hidden = false;
    $("#account-form").elements.display_name.value = me.user.display_name || "";
    $("#account-form").addEventListener("submit", saveAccount);
    $("#user-subscriptions-section").hidden = false;
    $("#subscription-form").addEventListener("submit", saveOwnSubscription);
    $("#cancel-edit").addEventListener("click", resetOwnSubscriptionForm);
    await loadOwnSubscriptions();
    return;
  }

  $("#admin-area").hidden = false;
  initialiseAdminNavigation();
  $("#refresh-logs").addEventListener("click", refreshOperations);
  ["start", "stop", "restart"].forEach((action) => {
    $(`#collector-${action}`).addEventListener("click", () => controlCollector(action));
  });
  $("#user-form").addEventListener("submit", saveUser);
  $("#admin-subscription-form").addEventListener("submit", saveAdminSubscription);
  $("#cancel-user").addEventListener("click", resetUserForm);
  $("#cancel-subscription").addEventListener("click", resetAdminSubscriptionForm);
  await Promise.all([loadSettings(), refreshOperations()]);
  startLogAutoRefresh();
  adminUsers = await getJson("/api/admin/users");
  renderUsers();
  adminSubscriptions = await getJson("/api/admin/subscriptions");
  renderAdminSubscriptions();
}

document.addEventListener("DOMContentLoaded", async () => {
  initialiseUserMenu();
  initialiseAccountModal();
  initialiseLogsModal();
  $("#logout").addEventListener("click", logout);
  settingsForms().forEach((form) => form.addEventListener("submit", saveSettings));
  installValidationNotifications();
  try {
    await initialise();
  } catch (error) {
    showMessage(error.message, true);
  }
});
