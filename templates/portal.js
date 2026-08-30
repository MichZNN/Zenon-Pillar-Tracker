let csrfToken = "";
let ownSubscriptions = [];
let adminUsers = [];
let adminSubscriptions = [];

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
  document.body.classList.toggle("modal-open", open);
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
  return (events || []).map((event) => EVENT_LABELS[event] || event).join(", ");
}

function subscriptionDestinations(item) {
  const destinations = [];
  if (item.channel_id) destinations.push(`Telegram: ${item.channel_id}`);
  if (item.discord_webhook) destinations.push("Discord webhook");
  return destinations.join(" · ") || "No destination";
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
    target.innerHTML = '<tr><td colspan="5" class="empty-state">No subscriptions yet.</td></tr>';
    return;
  }
  target.innerHTML = ownSubscriptions.map((item) =>
    `<tr><td><strong>${escapeHtml(item.label || subscriptionDestinations(item))}</strong>${item.label ? `<small>${escapeHtml(subscriptionDestinations(item))}</small>` : ""}</td>` +
    `<td>${escapeHtml((item.pillar_owner_addresses || []).join(", ") || "Network events")}</td>` +
    `<td>${escapeHtml(displayEvents(item.events))}</td>` +
    `<td><span class="status-badge ${item.active ? "active" : "inactive"}">${item.active ? "Active" : "Inactive"}</span></td>` +
    `<td><button class="ghost-button small-button" data-edit-own="${item.id}" type="button">Edit</button></td></tr>`
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
    `<tr><td>${escapeHtml(item.owner_username || "Unassigned")}</td><td><strong>${escapeHtml(item.label || subscriptionDestinations(item))}</strong>${item.label ? `<small>${escapeHtml(subscriptionDestinations(item))}</small>` : ""}</td>` +
    `<td>${escapeHtml((item.pillar_owner_addresses || []).join(", ") || "Network events")}</td>` +
    `<td>${escapeHtml(displayEvents(item.events))}</td><td><span class="status-badge ${item.active ? "active" : "inactive"}">${item.active ? "Active" : "Inactive"}</span></td>` +
    `<td><button class="ghost-button small-button" data-edit-subscription="${item.id}" type="button">Edit</button></td></tr>`
  ).join("") : '<tr><td colspan="6" class="empty-state">No subscriptions.</td></tr>';
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

async function loadLogs() {
  const payload = await getJson("/api/admin/logs?limit=100");
  renderCollectorDiagnostics(payload.collector);
  const file = payload.file || {};
  const configuredPath = file.configured_path && file.configured_path !== file.path
    ? ` · configured: ${file.configured_path}` : "";
  $("#log-file-info").textContent = `${file.path || "Log file"} · ${file.exists ? (file.size_bytes || 0) + " bytes" : "not created"}${configuredPath}`;
  const fileNotice = file.error ? `${file.error}\n\n` : "";
  $("#file-log").textContent = fileNotice + ((file.lines || []).join("\n") || "No log entries yet.");
  $("#audit-list").innerHTML = (payload.audit || []).map((item) =>
    `<tr><td>${escapeHtml(item.created_at)}</td><td>${escapeHtml(item.username || "system")}</td><td>${escapeHtml(item.action)}</td><td>${escapeHtml(item.entity_type + (item.entity_id ? " #" + item.entity_id : ""))}</td><td>${escapeHtml(JSON.stringify(item.details || {}))}</td></tr>`
  ).join("") || '<tr><td colspan="5" class="empty-state">No audit entries.</td></tr>';
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
    const payload = await getJson("/api/admin/collector-logs?tail=200");
    renderCollectorControl(payload);
    const collector = payload.collector || {};
    info.textContent = collector.name
      ? `${collector.name} · ${collector.status || collector.state || ""}`
      : "Collector container logs";
    viewer.textContent = payload.logs || "No collector container logs yet.";
  } catch (error) {
    info.textContent = "Collector container logs";
    viewer.textContent = `Could not load collector logs: ${error.message}`;
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
    await Promise.all([loadCollectorLogs(), loadLogs()]);
  } catch (error) {
    showMessage(error.message, true);
    await loadCollectorControl();
    await loadCollectorLogs();
  }
}

async function refreshOperations() {
  await Promise.all([loadLogs(), loadCollectorControl(), loadCollectorLogs()]);
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
  adminUsers = await getJson("/api/admin/users");
  renderUsers();
  adminSubscriptions = await getJson("/api/admin/subscriptions");
  renderAdminSubscriptions();
}

document.addEventListener("DOMContentLoaded", async () => {
  initialiseUserMenu();
  initialiseAccountModal();
  $("#logout").addEventListener("click", logout);
  settingsForms().forEach((form) => form.addEventListener("submit", saveSettings));
  installValidationNotifications();
  try {
    await initialise();
  } catch (error) {
    showMessage(error.message, true);
  }
});
