/* Всплывающие уведомления, рождённые в браузере («Ссылка скопирована»). */

/**
 * Показать уведомление.
 *
 * @param {string} message текст сообщения
 * @param {"info"|"success"|"warning"|"error"} kind вид сообщения
 * @param {number} timeout время показа в миллисекундах
 */
export function show(message, kind = "info", timeout = 5000) {
  let region = document.querySelector(".toast-region");
  if (!region) {
    region = document.createElement("div");
    region.className = "toast-region";
    region.setAttribute("role", "status");
    region.setAttribute("aria-live", "polite");
    document.body.appendChild(region);
  }

  const element = document.createElement("div");
  element.className = `toast toast--${kind}`;
  element.textContent = message;
  region.appendChild(element);

  window.setTimeout(() => element.remove(), timeout);
}
