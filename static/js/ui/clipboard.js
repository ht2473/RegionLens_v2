/* Копирование адреса из поля только для чтения кнопкой; без сценариев — вручную. */

import * as toasts from "../lib/toasts.js";

/** Скопировать выделенное старой командой документа. */
function legacy() {
  try {
    return document.execCommand("copy");
  } catch (error) {
    return false;
  }
}

/**
 * Скопировать значение поля; без защищённого соединения — старой командой.
 *
 * @param {HTMLInputElement} field поле с адресом
 * @returns {Promise<boolean>} удалось ли скопировать
 */
function write(field) {
  field.select();
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(field.value).then(
      () => true,
      () => legacy()
    );
  }
  return Promise.resolve(legacy());
}

/** Подключить обработчики на документе: поля приходят с фрагментами холста. */
export function init() {
  document.body.addEventListener("click", (event) => {
    const button = event.target.closest("[data-copy]");
    if (!button) {
      return;
    }
    const field = document.querySelector(button.dataset.copy);
    if (!field) {
      return;
    }
    write(field).then((done) => {
      if (done) {
        toasts.show(button.dataset.copyDone || field.value, "success", 2500);
      }
    });
  });

  // Поле с адресом выделяется целиком по первому щелчку.
  document.body.addEventListener("focusin", (event) => {
    const field = event.target.closest("[data-select-all]");
    if (field) {
      field.select();
    }
  });
}
