/* Экран мастера: тихое обновление, когда меняется любой активный заказ. */
(function () {
  "use strict";

  const root = document.querySelector("[data-master-pulse]");
  if (!root) return;

  const url = root.dataset.masterPulse;
  const signature = root.dataset.masterSignature || "";
  let busy = false;

  function isEditing() {
    const active = document.activeElement;
    if (!active) return false;
    return active.matches("input, textarea, select");
  }

  async function check() {
    if (busy || document.hidden || isEditing()) return;
    busy = true;
    try {
      const response = await fetch(url, { cache: "no-store" });
      if (!response.ok) return;
      const data = await response.json();
      if (data.signature && data.signature !== signature) {
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
