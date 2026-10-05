/* Кнопка «Печать или PDF» ([data-print]): окно печати браузера; PDF — его средствами. */

/** Подключить поведение; повторный вызов ничего не делает. */
export function init() {
  if (document.body.dataset.printReady === "true") {
    return;
  }
  document.body.dataset.printReady = "true";
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-print]");
    if (button) {
      window.print();
    }
  });
}
