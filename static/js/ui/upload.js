/* Загрузка своей таблицы: размер файла проверяется до отправки, при вставке из буфера
   сохраняется разметка таблицы (в ней видны объединённые клетки шапки), сводка ошибок
   получает фокус. Без сценариев формы работают так же — проверяет сервер. */

/** Не отправлять файл больше предела: тот же текст, что ответил бы сервер. */
function guardSize(form) {
  const input = form.querySelector("input[type=file][data-max-bytes]");
  const message = form.querySelector("[data-size-error]");
  if (!input || !message) {
    return;
  }
  const limit = Number(input.dataset.maxBytes);
  const check = () => {
    const file = input.files && input.files[0];
    const tooLarge = Boolean(file && file.size > limit);
    message.hidden = !tooLarge;
    input.setCustomValidity(tooLarge ? message.textContent.trim() : "");
    input.toggleAttribute("aria-invalid", tooLarge);
    return !tooLarge;
  };
  input.addEventListener("change", check);
  form.addEventListener("submit", (event) => {
    if (!check()) {
      event.preventDefault();
      input.focus();
    }
  });
}

/** Сохранить разметку таблицы из буфера в скрытое поле формы вставки. */
function keepMarkup(textarea) {
  const form = textarea.form;
  const markup = form && form.querySelector("input[name=markup]");
  if (!markup) {
    return;
  }
  textarea.addEventListener("paste", (event) => {
    const html = event.clipboardData ? event.clipboardData.getData("text/html") : "";
    markup.value = html && /<table/i.test(html) ? html : "";
  });
  textarea.addEventListener("input", () => {
    if (!textarea.value.trim()) {
      markup.value = "";
    }
  });
}

/** Подключить поведение на странице; повторный вызов ничего не делает. */
export function init(root = document) {
  root.querySelectorAll("form[data-upload-form]").forEach((form) => {
    if (form.dataset.uploadReady !== "true") {
      form.dataset.uploadReady = "true";
      guardSize(form);
    }
  });
  root.querySelectorAll("textarea.textarea--table").forEach((textarea) => {
    if (textarea.dataset.pasteReady !== "true") {
      textarea.dataset.pasteReady = "true";
      keepMarkup(textarea);
    }
  });
  const summary = root.querySelector("[data-error-summary]");
  if (summary) {
    summary.focus();
  }
}
