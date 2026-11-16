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

let loginChallenge = null;
let usingRecoveryCode = false;

function showStep(step) {
  $("#login-step").hidden = step !== "login";
  $("#two-factor-step").hidden = step !== "two-factor";
}

function setRecoveryMode(enabled) {
  usingRecoveryCode = enabled;
  const input = $("#login-code");
  input.value = "";
  input.placeholder = enabled ? "Recovery code" : "6-digit code";
  input.inputMode = enabled ? "text" : "numeric";
  input.autocomplete = enabled ? "off" : "one-time-code";
  $("#two-factor-intro").textContent = enabled
    ? "Enter one of the recovery codes you saved when you turned on two-factor authentication. Each code works once."
    : "Open your authenticator app and enter the 6-digit code for Zenon Pillar Tracker.";
  $("#use-recovery-code").textContent = enabled
    ? "Use the authenticator app instead"
    : "Use a recovery code instead";
  input.focus();
}

function backToLogin(messageText = "") {
  loginChallenge = null;
  $("#login-form").elements.password.value = "";
  $("#two-factor-form").reset();
  setRecoveryMode(false);
  showStep("login");
  $("#login-message").textContent = messageText;
  $("#login-form").elements.password.focus();
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
      const result = await getJson("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: form.get("username"),
          password: form.get("password"),
        }),
      });
      if (result.two_factor_required) {
        // Password is correct; continue on the second screen.
        loginChallenge = result.challenge;
        message.textContent = "";
        showStep("two-factor");
        setRecoveryMode(false);
        return;
      }
      location.replace(nextLocation());
    } catch (error) {
      message.textContent = error.message;
    }
  });

  $("#two-factor-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const message = $("#login-message");
    message.textContent = "Checking…";
    try {
      await getJson("/api/auth/login/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          challenge: loginChallenge,
          code: form.elements.code.value,
          trust_device: form.elements.trust_device.checked,
        }),
      });
      location.replace(nextLocation());
    } catch (error) {
      if (error.payload?.restart) {
        backToLogin(error.message);
        return;
      }
      message.textContent = error.message;
      form.elements.code.select();
    }
  });
  $("#use-recovery-code").addEventListener("click", () => setRecoveryMode(!usingRecoveryCode));
  $("#two-factor-back").addEventListener("click", () => backToLogin());
}

document.addEventListener("DOMContentLoaded", initialiseLogin);
