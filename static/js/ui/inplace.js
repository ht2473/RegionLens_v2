/* Правка на месте ([data-inplace-url]): текст по нажатию становится полем; Enter или уход
   фокуса — сохранить запросом POST (поле data-inplace-name, ответ — JSON {value} или {error}),
   Esc — отменить. Ссылка с data-inplace-edit="id" открывает правку того элемента вместо
   перехода (без сценариев — страница правки). Кнопка-пометка без значения показывает
   data-inplace-placeholder. Исследование правит своё поле в study.js. */

import * as toasts from "../lib/toasts.js";

function csrf() {
  try {
    return JSON.parse(document.body.getAttribute("hx-headers") || "{}")["X-CSRFToken"] || "";
  } catch {
    return "";
  }
}

/** Значение элемента: у пометки — из атрибута, у названия — его текст. */
function valueOf(target) {
  return target.dataset.inplaceValue ?? target.textContent.trim();
}

function show(target, value) {
  if ("inplaceValue" in target.dataset) {
    target.dataset.inplaceValue = value;
    target.textContent = value || target.dataset.inplacePlaceholder || "";
    target.classList.toggle("is-empty", !value);
  } else {
    target.textContent = value;
  }
}

function save(target, value) {
  const before = valueOf(target);
  show(target, value);
  const body = new FormData();
  body.append(target.dataset.inplaceName, value);
  fetch(target.dataset.inplaceUrl, {
    method: "POST",
    body,
    credentials: "same-origin",
    headers: { "X-CSRFToken": csrf(), Accept: "application/json" },
  })
    .then((response) => response.json().then((data) => ({ ok: response.ok, data })))
    .then(({ ok, data }) => {
      if (!ok) {
        show(target, before);
        toasts.show(data.error || "", "error");
        return;
      }
      show(target, data.value ?? value);
    })
    .catch(() => {
      show(target, before);
      toasts.show(target.dataset.inplaceFailed || "", "error");
    });
}

function start(target) {
  if (!target || target.hidden) {
    return;
  }
  const field = document.createElement("input");
  field.className = "input inplace__field";
  field.value = valueOf(target);
  field.maxLength = Number(target.dataset.inplaceMax || 200);
  field.setAttribute("aria-label", target.dataset.inplaceLabel || "");
  target.hidden = true;
  target.after(field);
  field.focus();
  field.select();

  let done = false;
  const finish = (keep) => {
    if (done) {
      return;
    }
    done = true;
    const value = field.value.trim();
    field.remove();
    target.hidden = false;
    target.focus();
    if (keep && value !== valueOf(target)) {
      save(target, value);
    }
  };
  field.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      finish(false);
    } else if (event.key === "Enter") {
      event.preventDefault();
      finish(true);
    }
  });
  field.addEventListener("blur", () => finish(true));
}

function onClick(event) {
  const opener = event.target.closest?.("[data-inplace-edit]");
  if (opener) {
    event.preventDefault();
    start(document.getElementById(opener.dataset.inplaceEdit));
    return;
  }
  const target = event.target.closest?.("button[data-inplace-url]");
  if (target) {
    event.preventDefault();
    start(target);
  }
}

/** Открыть кнопки правки: без сценариев видна только сохранённая пометка текстом. */
export function enhance(root = document) {
  root.querySelectorAll("button[data-inplace-url][hidden]").forEach((button) => {
    button.hidden = false;
    const text = button.nextElementSibling;
    if (text?.matches("[data-inplace-static]")) {
      text.remove();
    }
  });
}

export function init() {
  enhance(document);
  document.addEventListener("click", onClick);
}
