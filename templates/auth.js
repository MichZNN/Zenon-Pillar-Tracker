function $(selector) {
  return document.querySelector(selector);
}

async function getJson(url, options = {}) {
  const response = await fetch(url, { cache: "no-store", ...options });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Request failed");
  return payload;
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
        }),
      });
      location.replace(nextLocation());
    } catch (error) {
      message.textContent = error.message;
    }
  });
}

document.addEventListener("DOMContentLoaded", initialiseLogin);
