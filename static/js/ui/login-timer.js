/* Срок ввода кода входа ([data-login-timer]): за две минуты до конца — предупреждение,
   которое объявляется программе чтения, и кнопка «Продлить» (WCAG 2.2.1). Продление идёт
   запросом, набранные цифры остаются в поле. Время вышло — ссылка на вход заново. */

import { say } from "../lib/announce.js";

/** Подключить поведение; повторный вызов ничего не делает. */
export function init() {
  const box = document.querySelector("[data-login-timer]");
  if (!box || box.dataset.timerReady === "true") {
    return;
  }
  box.dataset.timerReady = "true";

  const soon = box.querySelector("[data-login-timer-soon]");
  const over = box.querySelector("[data-login-timer-over]");
  const extend = box.querySelector("[data-login-timer-extend]");
  const warning = Number(box.dataset.warning) * 1000;
  let warnTimer;
  let overTimer;

  /** Запустить отсчёт заново: срок — секунды от сервера. */
  const start = (seconds) => {
    window.clearTimeout(warnTimer);
    window.clearTimeout(overTimer);
    box.hidden = true;
    const left = seconds * 1000;
    warnTimer = window.setTimeout(() => {
      box.hidden = false;
      say(soon.textContent.trim());
    }, Math.max(0, left - warning));
    overTimer = window.setTimeout(() => {
      box.hidden = false;
      soon.hidden = true;
      over.hidden = false;
      if (extend) {
        extend.hidden = true;
      }
      say(over.textContent.replace(/\s+/g, " ").trim());
    }, left);
  };

  extend?.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const response = await fetch(window.location.href, {
        method: "POST",
        body: new FormData(extend),
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      const timer = response.ok ? await response.json() : null;
      if (!timer || !timer.seconds) {
        extend.submit();
        return;
      }
      if (!timer.extensions) {
        extend.remove();
      }
      start(timer.seconds);
      document.querySelector("input[name='code']")?.focus();
    } catch {
      // Сеть недоступна — обычная отправка покажет страницу с ответом сервера.
      extend.submit();
    }
  });

  start(Number(box.dataset.seconds));
}
