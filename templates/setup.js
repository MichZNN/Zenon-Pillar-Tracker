function $(selector) {
  return document.querySelector(selector);
}

async function getJson(url, options = {}) {
  const response = await fetch(url, { cache: "no-store", ...options });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Request failed");
  return payload;
}

async function initialiseSetup() {
  $("#setup-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const message = $("#setup-message");
    message.className = "form-message";
    message.textContent = "Creating administrator…";

    try {
      await getJson("/api/setup/admin", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: form.get("username"),
          display_name: form.get("display_name"),
          password: form.get("password"),
          password_confirmation: form.get("password_confirmation"),
        }),
      });
      message.textContent = "Administrator created. Opening the portal…";
      location.replace("/portal");
    } catch (error) {
      message.className = "form-message error";
      message.textContent = error.message;
    }
  });
}

document.addEventListener("DOMContentLoaded", initialiseSetup);
