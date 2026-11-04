/* Форма вернулась с ошибками ([aria-invalid="true"]): фокус — на первое поле с ошибкой.
   Иначе после перезагрузки страницы программа чтения начинает с шапки и не сообщает,
   что форма не принята (WCAG 3.3.1). Страницы со сводкой ошибок ([data-error-summary])
   ставят фокус на сводку сами (upload.js). */

/** Подключить поведение; повторный вызов ничего не делает. */
export function init() {
  if (document.body.dataset.invalidReady === "true") {
    return;
  }
  document.body.dataset.invalidReady = "true";
  if (document.querySelector("[data-error-summary]")) {
    return;
  }
  const field = [...document.querySelectorAll("[aria-invalid='true']")].find(
    (element) => element.getClientRects().length > 0,
  );
  field?.focus();
}
