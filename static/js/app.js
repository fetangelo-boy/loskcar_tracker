/* Общие помощники: CSRF, toast, подтверждения. */
(function () {
  "use strict";

  function getCookie(name) {
    const parts = document.cookie ? document.cookie.split(";") : [];
    for (const part of parts) {
      const trimmed = part.trim();
      if (trimmed.startsWith(name + "=")) {
        return decodeURIComponent(trimmed.slice(name.length + 1));
      }
    }
    return "";
  }

  window.getCsrfToken = function () {
    return getCookie("csrftoken");
  };

  let toastTimer = null;

  window.showToast = function (message, isError) {
    const node = document.getElementById("toast");
    if (!node) return;
    node.textContent = message;
    node.classList.toggle("is-error", Boolean(isError));
    node.hidden = false;
    if (toastTimer) window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(function () {
      node.hidden = true;
    }, 4200);
  };

  document.addEventListener(
    "submit",
    function (event) {
      const form = event.target;
      if (!(form instanceof HTMLFormElement)) return;
      const message = form.getAttribute("data-confirm");
      if (message && !window.confirm(message)) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    },
    true
  );

  /* Фильтры календаря отправляются сразу при смене значения. */
  document.addEventListener("change", function (event) {
    const field = event.target;
    if (!(field instanceof HTMLSelectElement)) return;
    if (!field.hasAttribute("data-submit-on-change")) return;
    if (field.form) field.form.submit();
  });

  window.setTimeout(function () {
    document.querySelectorAll(".flash").forEach(function (node) {
      node.style.transition = "opacity .25s";
      node.style.opacity = "0";
      window.setTimeout(function () {
        node.remove();
      }, 260);
    });
  }, 7000);
})();
