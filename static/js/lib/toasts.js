/* Всплывающие уведомления: рождённые в браузере («Ссылка скопирована») и пришедшие
   с сервера (сообщения после перехода). Успех и сведения исчезают сами, при наведении
   и фокусе время останавливается; ошибка остаётся, пока её не закроют. У каждого — кнопка
   «Закрыть». */

// Сколько показывать: успех и сведения — 6 с, предупреждение — 10 с, ошибка — до закрытия.
const TIMEOUTS = { success: 6000, info: 6000, warning: 10000, error: 0 };

/** Подпись кнопки закрытия — из области уведомлений (переведена на сервере). */
function closeLabel(region) {
  return (region && region.dataset.closeLabel) || "Закрыть";
}

/** Область уведомлений страницы; создаётся при первом уведомлении из браузера. */
function region() {
  let found = document.querySelector(".toast-region");
  if (!found) {
    found = document.createElement("div");
    found.className = "toast-region";
    found.setAttribute("role", "status");
    found.setAttribute("aria-live", "polite");
    document.body.appendChild(found);
  }
  return found;
}

/** Вид уведомления по его классу: toast--success и т. п. */
function kindOf(element) {
  const match = /toast--(\w+)/.exec(element.className);
  return match ? match[1] : "info";
}

/**
 * Подготовить уведомление: кнопка «Закрыть» и таймер с паузой при наведении и фокусе.
 *
 * @param {HTMLElement} element уведомление
 * @param {number} [timeout] время показа в миллисекундах; 0 — до закрытия
 */
export function arm(element, timeout) {
  if (element.dataset.armed) {
    return;
  }
  element.dataset.armed = "1";
  const delay = timeout === undefined ? (TIMEOUTS[kindOf(element)] ?? 6000) : timeout;
  if (!element.querySelector(".toast__close")) {
    const text = document.createElement("span");
    text.className = "toast__text";
    while (element.firstChild) {
      text.appendChild(element.firstChild);
    }
    const close = document.createElement("button");
    close.type = "button";
    close.className = "toast__close";
    close.setAttribute("aria-label", closeLabel(element.closest(".toast-region")));
    close.textContent = "×";
    close.addEventListener("click", () => element.remove());
    element.append(text, close);
  }
  if (!delay) {
    return;
  }
  let left = delay;
  let started = Date.now();
  let timer = window.setTimeout(() => element.remove(), left);
  const pause = () => {
    window.clearTimeout(timer);
    left -= Date.now() - started;
  };
  const resume = () => {
    if (element.matches(":hover") || element.contains(document.activeElement)) {
      return;
    }
    started = Date.now();
    timer = window.setTimeout(() => element.remove(), Math.max(left, 1500));
  };
  element.addEventListener("mouseenter", pause);
  element.addEventListener("focusin", pause);
  element.addEventListener("mouseleave", resume);
  element.addEventListener("focusout", resume);
}

/**
 * Показать уведомление.
 *
 * @param {string} message текст сообщения
 * @param {"info"|"success"|"warning"|"error"} kind вид сообщения
 * @param {number} [timeout] время показа в миллисекундах; по умолчанию — по виду
 */
export function show(message, kind = "info", timeout = undefined) {
  const element = document.createElement("div");
  element.className = `toast toast--${kind}`;
  element.textContent = message;
  region().appendChild(element);
  arm(element, timeout);
}

/** Уведомления, пришедшие с сервера вместе со страницей или фрагментом. */
export function init(root = document) {
  root.querySelectorAll(".toast-region .toast, [data-toast]").forEach((element) => {
    const own = element.dataset.toastTimeout;
    arm(element, own === undefined ? undefined : Number(own));
  });
}
