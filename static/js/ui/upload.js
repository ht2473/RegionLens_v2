/* Загрузка своей таблицы: размер файла проверяется до отправки, файл можно перетащить
   на страницу, при вставке из буфера сохраняется разметка таблицы (в ней видны
   объединённые клетки шапки), сводка ошибок получает фокус. Без сценариев формы работают
   так же — файл выбирается кнопкой, проверяет сервер. */

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

/** Несут ли над страницей файлы, а не выделенный текст или ссылку. */
function carriesFiles(event) {
  return Array.from(event.dataTransfer?.types || []).includes("Files");
}

/**
 * Зона файла: имя выбранного файла, перетаскивание на страницу. ``wholePage`` — файл,
 * отпущенный в любом месте страницы, попадает в эту зону: на странице она одна такая.
 */
function dropZone(zone, wholePage) {
  const input = zone.querySelector("input[type=file]");
  const name = zone.querySelector("[data-file-name]");
  const form = input?.form;
  if (!input || !name || !form) {
    return;
  }
  const submit = zone.dataset.fileDrop === "submit";
  if (submit) {
    form.querySelectorAll("[data-file-drop-fallback]").forEach((item) => {
      item.hidden = true;
    });
  }

  input.addEventListener("change", () => {
    const file = input.files && input.files[0];
    name.hidden = !file;
    name.textContent = file ? file.name : "";
    // Проверка размера (guardSize) подписана раньше: при превышении поле недействительно.
    if (submit && file && input.checkValidity()) {
      name.textContent = `${zone.dataset.sending} «${file.name}»…`;
      zone.setAttribute("aria-busy", "true");
      form.requestSubmit();
    }
  });

  const target = wholePage ? document : zone;
  let depth = 0;
  const leave = () => {
    depth = 0;
    zone.classList.remove("is-dragging");
  };
  target.addEventListener("dragenter", (event) => {
    if (carriesFiles(event)) {
      depth += 1;
      zone.classList.add("is-dragging");
    }
  });
  target.addEventListener("dragleave", (event) => {
    if (carriesFiles(event)) {
      depth = Math.max(0, depth - 1);
      if (!depth) {
        leave();
      }
    }
  });
  target.addEventListener("dragover", (event) => {
    if (carriesFiles(event)) {
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
    }
  });
  target.addEventListener("drop", (event) => {
    if (!carriesFiles(event)) {
      return;
    }
    event.preventDefault();
    leave();
    if (event.dataTransfer.files.length) {
      input.files = event.dataTransfer.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
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
  const zones = Array.from(root.querySelectorAll("[data-file-drop]")).filter(
    (zone) => zone.dataset.dropReady !== "true",
  );
  zones.forEach((zone) => {
    zone.dataset.dropReady = "true";
    dropZone(zone, zones.length === 1);
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
