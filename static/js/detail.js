/* Карточка заказа: тихое обновление при изменении версии записи. */
(function () {
  "use strict";

  const layout = document.querySelector("[data-pulse-url]");
  if (!layout) return;

  const url = layout.dataset.pulseUrl;
  const version = Number(layout.dataset.orderVersion || "0");
  let busy = false;

  function isEditing() {
    const active = document.activeElement;
    if (!active) return false;
    if (active.matches("input, textarea, select")) return true;
    return Boolean(document.querySelector("details[open]"));
  }

  async function check() {
    if (busy || document.hidden || isEditing()) return;
    busy = true;
    try {
      const response = await fetch(url, { cache: "no-store" });
      if (!response.ok) return;
      const data = await response.json();
      if (Number(data.version) !== version) {
        window.location.reload();
      }
    } catch (error) {
      /* повтор на следующем тике */
    } finally {
      busy = false;
    }
  }

  window.setInterval(check, 5000);
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) check();
  });
})();
