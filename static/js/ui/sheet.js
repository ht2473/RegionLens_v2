/* Рейль нижним выдвижным листом на телефоне; правила листа включает признак data-rail
   на корне документа, без сценариев рейль — блок под холстом. */

// Ширина, ниже которой рейль — лист (как в layout.css).
const SHEET_WIDTH = "(max-width: 767px)";

/** Опознаватель раскрытого рейля или null. */
let openId = null;

/** Элемент раскрытого рейля, если он ещё в документе. */
function rail() {
  return openId ? document.getElementById(openId) : null;
}

/**
 * Привести полосу вызова, затемнение и блокировку прокрутки к состоянию листа.
 *
 * Вызывается и после замены фрагмента; унесённый заменой рейль считается закрытым.
 */
export function reflect() {
  const node = rail();
  const opened = Boolean(node && node.classList.contains("is-open"));
  if (!opened) {
    openId = null;
  }

  document.querySelectorAll("[data-rail-open]").forEach((button) => {
    button.setAttribute("aria-expanded", opened ? "true" : "false");
  });
  document.querySelectorAll("[data-rail-backdrop]").forEach((backdrop) => {
    backdrop.hidden = !opened;
  });
  document.body.classList.toggle("is-rail-open", opened);
}

/** Раскрыть рейль листом. */
function open(node) {
  if (!node) {
    return;
  }
  openId = node.id;
  node.classList.add("is-open");
  reflect();

  // Фокус — на выход из листа.
  const done = node.querySelector("[data-rail-close]");
  if (done) {
    done.focus();
  }
}

/** Закрыть лист и вернуть фокус тому, кто его открыл. */
function close({ focus = true } = {}) {
  const node = rail();
  if (node) {
    node.classList.remove("is-open");
  }
  openId = null;
  reflect();

  const opener = document.querySelector("[data-rail-open]");
  if (focus && opener) {
    opener.focus();
  }
}

/** Подключить полосу вызова, если она есть на странице. */
export function init() {
  if (!document.querySelector("[data-rail-open]")) {
    return;
  }
  document.documentElement.dataset.rail = "sheet";

  // Слушатель на документе: полоса и рейль приходят фрагментами.
  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!target.closest) {
      return;
    }
    const opener = target.closest("[data-rail-open]");
    if (opener) {
      event.preventDefault();
      const node = document.getElementById(opener.getAttribute("aria-controls"));
      if (node && node.classList.contains("is-open")) {
        close();
      } else {
        open(node);
      }
      return;
    }
    if (target.closest("[data-rail-close]") || target.closest("[data-rail-backdrop]")) {
      event.preventDefault();
      close();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && openId) {
      close();
    }
  });

  // На широком экране лист закрывается, чтобы снять блокировку прокрутки.
  window.matchMedia(SHEET_WIDTH).addEventListener("change", (event) => {
    if (!event.matches && openId) {
      close({ focus: false });
    }
  });
}
