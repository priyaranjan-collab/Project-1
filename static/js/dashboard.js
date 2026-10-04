document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-poll-url]").forEach((dashboard) => {
    const refresh = async () => {
      try {
        const response = await fetch(dashboard.dataset.pollUrl, {
          headers: { Accept: "application/json" },
          credentials: "same-origin",
        });
        if (!response.ok) return;
        const values = await response.json();
        Object.entries(values).forEach(([key, value]) => {
          document.querySelectorAll(`[data-stat="${key}"]`).forEach((element) => {
            element.textContent = value;
          });
        });
      } catch (_error) {
        // Polling is an enhancement; the server-rendered dashboard remains usable.
      }
    };
    window.setInterval(refresh, Number(dashboard.dataset.pollInterval) || 12000);
  });

  document.querySelectorAll("[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });

  document.querySelectorAll(".flash-close").forEach((button) => {
    button.addEventListener("click", () => button.closest(".flash")?.remove());
  });
});