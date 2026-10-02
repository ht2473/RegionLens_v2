/* Объявление программе чтения с экрана о заменённой области с признаком data-announce
   (WCAG 2.2, 4.1.3 «Сообщения о состоянии»). */

/** Задержка перед объявлением: без неё замена области его перебивает. */
const DELAY = 200;

/** Сказать вслух, что область пересчитана. */
export function say(message) {
  const region = document.getElementById("live-status");
  if (!region || !message) {
    return;
  }
  // Очистка перед записью: тот же текст без неё не объявляется повторно.
  region.textContent = "";
  window.setTimeout(() => {
    region.textContent = message;
  }, DELAY);
}

/** Собрать объявление: что произошло (data-announce) и над чем (data-announce-subject). */
export function fromSwap(target) {
  if (!target || !target.matches) {
    return;
  }
  const marked = target.matches("[data-announce]") ? target : target.closest("[data-announce]");
  if (!marked) {
    return;
  }

  const event = marked.dataset.announce.trim();
  const named = marked.querySelector("[data-announce-subject]");
  const subject = named ? named.textContent.replace(/\s+/g, " ").trim() : "";
  say(subject ? `${event}: ${subject}` : event);
}
