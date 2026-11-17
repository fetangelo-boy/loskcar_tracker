/* Канбан: drag-and-drop на ПК, polling фрагмента доски и явная смена статуса. */
(function () {
  "use strict";

  let board = document.getElementById("board");
  if (!board) return;

  const POLL_INTERVAL = 5000;
  let dragging = null;
  let refreshing = false;

  function notify(message, isError) {
    if (typeof window.showToast === "function") {
      window.showToast(message, isError);
    }
  }

  document.addEventListener("dragstart", function (event) {
    const card = event.target.closest(".kanban-card");
    if (!card) return;
    dragging = card;
    card.classList.add("is-dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", card.dataset.orderId || "");
  });

  document.addEventListener("dragend", function () {
    if (dragging) dragging.classList.remove("is-dragging");
    clearDragOver();
    window.setTimeout(function () {
      dragging = null;
    }, 0);
  });

  function clearDragOver() {
    document.querySelectorAll(".kanban-column.drag-over").forEach(function (column) {
      column.classList.remove("drag-over");
    });
  }

  document.addEventListener("dragover", function (event) {
    const zone = event.target.closest(".kanban-cards");
    if (!zone || !dragging) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    clearDragOver();
    const column = zone.closest(".kanban-column");
    if (column) column.classList.add("drag-over");
  });

  document.addEventListener("dragleave", function (event) {
    const zone = event.target.closest(".kanban-cards");
    if (!zone) return;
    if (!zone.contains(event.relatedTarget)) {
      const column = zone.closest(".kanban-column");
      if (column) column.classList.remove("drag-over");
    }
  });

  document.addEventListener("drop", async function (event) {
    const zone = event.target.closest(".kanban-cards");
    if (!zone || !dragging) return;
    event.preventDefault();
    const card = dragging;
    const target = zone.dataset.dropStatus;
    clearDragOver();
    if (!target || target === card.dataset.status) return;
    await changeStatus(card, target);
  });

  async function changeStatus(card, target) {
    const url = card.dataset.statusUrl;
    if (!url) return;
    const body = new URLSearchParams({
      to: target,
      version: card.dataset.version || "",
      next: window.location.pathname + window.location.search
    });
    try {
      const response = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
          "X-CSRFToken": window.getCsrfToken ? window.getCsrfToken() : "",
          "X-Requested-With": "fetch"
        },
        body: body
      });
      let data = {};
      try {
        data = await response.json();
      } catch (error) {
        data = {};
      }
      if (response.ok && data.ok) {
        notify("Статус изменён: " + data.status_label);
      } else {
        notify(data.error || "Не удалось изменить статус.", true);
      }
    } catch (error) {
      notify("Нет связи с сервером. Обновите страницу.", true);
    }
    await refreshBoard(true);
  }

  async function refreshBoard(force) {
    if (!board || refreshing) return;
    if (!force) {
      if (dragging) return;
      const active = document.activeElement;
      if (active && board.contains(active) && active.matches("select, input, textarea, button, summary")) {
        return;
      }
    }
    refreshing = true;
    try {
      const url = new URL(board.dataset.fragmentUrl, window.location.origin);
      url.search = window.location.search;
      const response = await fetch(url, {
        headers: { "X-Requested-With": "fetch" },
        cache: "no-store"
      });
      if (!response.ok) return;
      const html = await response.text();
      const template = document.createElement("template");
      template.innerHTML = html.trim();
      const next = template.content.firstElementChild;
      if (!next || next.id !== "board") return;
      if (next.dataset.signature === board.dataset.signature) return;

      const oldScroll = board.querySelector(".board-scroll");
      const left = oldScroll ? oldScroll.scrollLeft : 0;
      board.replaceWith(next);
      board = next;
      const newScroll = board.querySelector(".board-scroll");
      if (newScroll) newScroll.scrollLeft = left;
    } catch (error) {
      /* тихо: повтор на следующем тике */
    } finally {
      refreshing = false;
    }
  }

  window.setInterval(function () {
    if (document.hidden) return;
    refreshBoard(false);
  }, POLL_INTERVAL);

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) refreshBoard(false);
  });
})();
