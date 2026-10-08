/* Страница таблицы, вкладка «Данные»: выбранный показатель отмечается в списке сразу,
   правая часть приходит запросом HTMX. На телефоне видно либо список, либо выбранное:
   выбор открывает выбранное, «Все показатели» возвращает список. Без сценариев — переходы. */

const browser = () => document.querySelector("[data-dataset-browser]");

function onPick(event) {
  const link = event.target.closest("[data-dataset-pick]");
  const root = browser();
  if (!link || !root) {
    return;
  }
  root.querySelectorAll("[data-dataset-pick]").forEach((item) => {
    if (item === link) {
      item.setAttribute("aria-current", "true");
    } else {
      item.removeAttribute("aria-current");
    }
  });
  root.classList.add("is-picked");
}

function onBack(event) {
  const link = event.target.closest("[data-dataset-back]");
  const root = browser();
  if (!link || !root) {
    return;
  }
  event.preventDefault();
  root.classList.remove("is-picked");
  root.querySelector("[aria-current='true']")?.scrollIntoView({ block: "center" });
}

export function init() {
  if (!browser()) {
    return;
  }
  document.addEventListener("click", onPick);
  document.addEventListener("click", onBack);
  // Выбранное пришло: если его начало не видно (телефон, долгая прокрутка) — к нему.
  document.body.addEventListener("htmx:afterSwap", () => {
    const detail = document.getElementById("dataset-detail");
    const top = detail ? detail.getBoundingClientRect().top : 0;
    if (detail && (top < 0 || top > window.innerHeight / 2)) {
      detail.scrollIntoView({ block: "start" });
    }
  });
}
