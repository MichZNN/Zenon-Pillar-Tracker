function $(selector) {
  return document.querySelector(selector);
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

function showTwoFactorField() {
  const field = $("#two-factor-field");
  if (!field.hidden) return;
  field.hidden = false;
  $("#login-code").required = true;
  $("#login-code").focus();
}

function nextLocation() {
  const candidate = new URLSearchParams(location.search).get("next");
  return candidate && candidate.startsWith("/") && !candidate.startsWith("//")
    ? candidate
    : "/portal";
}

async function initialiseLogin() {
  const passwordInput = $("#login-password");
  const passwordToggle = $("#toggle-login-password");
  passwordToggle.addEventListener("click", () => {
    const showing = passwordInput.type === "password";
    passwordInput.type = showing ? "text" : "password";
    passwordToggle.setAttribute("aria-label", showing ? "Hide password" : "Show password");
    passwordToggle.setAttribute("aria-pressed", String(showing));
    const icon = passwordToggle.querySelector("i");
    icon.classList.toggle("fa-eye", !showing);
    icon.classList.toggle("fa-eye-slash", showing);
  });

  try {
    const me = await getJson("/api/auth/me");
    if (me.authenticated) location.replace(nextLocation());
  } catch (error) {
    $("#login-message").textContent = error.message;
  }
  $("#login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const message = $("#login-message");
    message.textContent = "Signing in…";
    try {
      await getJson("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: form.get("username"),
          password: form.get("password"),
          code: form.get("code") || "",
        }),
      });
      location.replace(nextLocation());
    } catch (error) {
      if (error.payload?.two_factor_required) {
        const firstAsk = $("#two-factor-field").hidden;
        showTwoFactorField();
        message.textContent = firstAsk
          ? "Enter the code from your authenticator app."
          : error.message;
        if (!firstAsk) $("#login-code").select();
        return;
      }
      message.textContent = error.message;
    }
  });
}

document.addEventListener("DOMContentLoaded", initialiseLogin);
