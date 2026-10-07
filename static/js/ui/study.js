/* Поле исследования: перетаскивание карточек и рядов панели (SortableJS), те же действия
   без мыши — меню «Выше» и «Ниже», ширина и порядок на месте, название и заметка правятся
   на месте, «Развернуть» во весь экран, мой регион своим цветом во всех карточках.
   Без сценариев все действия — формы с переходом обратно на страницу. */

const STATUS_DELAY = 2500;
const MINE_CLASS = "is-mine";

/** Корень страницы исследования; адрес правки есть только у владельца. */
const root = () => document.querySelector("[data-study]");

/** Заголовки запроса правки: признак сценария и токен CSRF из hx-headers страницы. */
function headers() {
  let token = "";
  try {
    token = JSON.parse(document.body.getAttribute("hx-headers") || "{}")["X-CSRFToken"] || "";
  } catch (error) {
    token = "";
  }
  return { "X-CSRFToken": token, "X-Study": "1" };
}

/** Строка «Сохранено» у сведений исследования; гаснет сама. */
let statusTimer = null;
function status(ok) {
  const page = root();
  const node = document.querySelector("[data-study-status]");
  if (!page || !node) {
    return;
  }
  node.textContent = ok ? page.dataset.labelSaved : page.dataset.labelFailed;
  node.classList.toggle("is-failed", !ok);
  window.clearTimeout(statusTimer);
  if (ok) {
    statusTimer = window.setTimeout(() => {
      node.textContent = "";
    }, STATUS_DELAY);
  }
}

/**
 * Отправить правку без перехода; вернуть, удалась ли она.
 *
 * @param {Record<string, string|string[]>} values поля формы
 */
async function save(values) {
  const page = root();
  if (!page || !page.dataset.study) {
    return false;
  }
  const body = new URLSearchParams();
  Object.entries(values).forEach(([name, value]) => {
    (Array.isArray(value) ? value : [value]).forEach((item) => body.append(name, item));
  });
  try {
    const response = await fetch(page.dataset.study, {
      method: "POST",
      headers: headers(),
      body,
      credentials: "same-origin",
    });
    status(response.ok);
    return response.ok;
  } catch (error) {
    status(false);
    return false;
  }
}

/* --- Порядок: перетаскивание и «Выше»/«Ниже» ------------------------------------------- */

const cards = () => document.getElementById("study-cards");

function order() {
  const field = cards();
  return field ? [...field.children].map((node) => node.dataset.block).filter(Boolean) : [];
}

/** Графики меняют размер вместе с карточкой. */
function refit() {
  window.dispatchEvent(new Event("resize"));
}

function bindSortable() {
  const field = cards();
  const Sortable = window.Sortable;
  if (!field || !Sortable || !root()?.dataset.study) {
    return;
  }
  field.querySelectorAll("[data-grip]").forEach((grip) => {
    grip.hidden = false;
  });
  if (!field.dataset.sortableReady) {
    field.dataset.sortableReady = "true";
    Sortable.create(field, {
      group: { name: "study", pull: false, put: true },
      handle: "[data-grip]",
      animation: 150,
      onEnd: (event) => {
        if (event.from === event.to && event.oldIndex !== event.newIndex) {
          save({ action: "reorder", order: order() });
        }
      },
      onAdd: (event) => {
        const key = event.item.dataset.series;
        const at = String(event.newIndex);
        event.item.remove();
        if (key) {
          drop(key, at);
        }
      },
    });
  }
  // Ряды панели — источник: перетаскивается копия, сама строка остаётся на месте.
  document.querySelectorAll("[data-drag-source]").forEach((list) => {
    if (list.dataset.sortableReady) {
      return;
    }
    list.dataset.sortableReady = "true";
    Sortable.create(list, {
      group: { name: "study", pull: "clone", put: false },
      sort: false,
      draggable: "[data-series]",
      delay: 200,
      delayOnTouchOnly: true,
    });
  });
}

/** Ряд, брошенный на поле: карта ряда в место броска. */
function drop(key, at) {
  const form = document.getElementById("study-drop");
  if (!form) {
    return;
  }
  form.elements.series.value = key;
  form.elements.at.value = at;
  form.dataset.at = at;
  form.requestSubmit();
}

/** Новая карточка пришла в конец поля: поставить в место броска и показать. */
function settleNew() {
  const field = cards();
  const fresh = field ? field.querySelector(".is-new:not([data-settled])") : null;
  if (!fresh) {
    return;
  }
  fresh.dataset.settled = "true";
  const form = document.getElementById("study-drop");
  const at = form && form.dataset.at ? Number(form.dataset.at) : null;
  if (at !== null && field.children[at] && field.children[at] !== fresh) {
    field.insertBefore(fresh, field.children[at]);
  }
  if (form) {
    delete form.dataset.at;
  }
  // На телефоне лист панели закрывается: новую карточку видно.
  const panel = document.querySelector(".study-panel.is-open [data-rail-close]");
  if (panel) {
    panel.click();
  }
  fresh.scrollIntoView({ behavior: "smooth", block: "center" });
}

/* --- Меню карточки: ширина, порядок, развернуть ---------------------------------------- */

function onMenuSubmit(event) {
  const form = event.target.closest("[data-card-actions]");
  const submitter = event.submitter;
  if (!form || !submitter || submitter.hasAttribute("hx-post")) {
    return;
  }
  const action = submitter.value;
  const block = form.elements.block.value;
  const card = document.getElementById(`block-${block}`);
  if (!card || !["resize", "up", "down"].includes(action)) {
    return;
  }
  event.preventDefault();
  form.closest("[popover]")?.hidePopover();
  if (action === "resize") {
    const wide = card.classList.toggle("study-card--wide");
    const label = submitter.querySelector("[data-wide-label]");
    if (label) {
      label.textContent = wide ? label.dataset.wideLabel : label.dataset.narrowLabel;
    }
    refit();
    save({ action: "resize", block });
    return;
  }
  const sibling = action === "up" ? card.previousElementSibling : card.nextElementSibling;
  if (!sibling) {
    return;
  }
  if (action === "up") {
    sibling.before(card);
  } else {
    sibling.after(card);
  }
  card.querySelector("[popovertarget]")?.focus();
  save({ action: "reorder", order: order() });
}

/** Название и пояснение карточки из меню: сохранить и перестроить карточку. */
function onChangeSubmit(event) {
  const form = event.target.closest("[data-card-change]");
  if (!form || !window.htmx) {
    return;
  }
  event.preventDefault();
  const values = Object.fromEntries(new FormData(form).entries());
  delete values.csrfmiddlewaretoken;
  form.closest("[popover]")?.hidePopover();
  save(values).then((ok) => {
    const card = document.getElementById(`block-${values.block}`);
    if (!ok || !card) {
      return;
    }
    const slot = card.querySelector(".study-card__slot");
    if (slot) {
      window.htmx.ajax("GET", slot.getAttribute("hx-get"), { target: slot, swap: "innerHTML" });
    }
    const note = card.querySelector(".study-note__body");
    if (note && values.text !== undefined) {
      note.textContent = values.text;
    }
  });
}

function bindExpand() {
  if (!document.fullscreenEnabled) {
    return;
  }
  document.querySelectorAll("[data-expand]").forEach((button) => {
    button.hidden = false;
  });
}

function onExpand(event) {
  const button = event.target.closest("[data-expand]");
  if (!button) {
    return;
  }
  const card = button.closest(".study-card");
  button.closest("[popover]")?.hidePopover();
  card?.requestFullscreen?.().catch(() => {});
}

/* --- Правка на месте: название исследования и карточки, текст заметки ------------------ */

function startInplace(button) {
  const kind = button.dataset.inplace;
  const multiline = kind === "text";
  const field = document.createElement(multiline ? "textarea" : "input");
  field.className = multiline ? "textarea inplace__field" : "input inplace__field";
  field.value = multiline ? button.innerText.trim() : button.textContent.trim();
  field.setAttribute("aria-label", button.getAttribute("aria-label") || "");
  field.maxLength = multiline ? 4000 : 200;
  if (multiline) {
    field.rows = Math.max(3, field.value.split("\n").length);
  }
  button.hidden = true;
  button.after(field);
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
    button.hidden = false;
    button.focus();
    if (!keep) {
      return;
    }
    const shown = value || button.dataset.default || "";
    if (multiline) {
      button.innerText = shown;
    } else {
      button.textContent = shown;
    }
    if (kind === "study-title") {
      if (value) {
        save({ action: "rename", title: value });
      }
      return;
    }
    save({ action: "change", block: button.dataset.block, [kind]: value });
  };
  field.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      finish(false);
    } else if (event.key === "Enter" && (!multiline || event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      finish(true);
    }
  });
  field.addEventListener("blur", () => finish(true));
}

function onInplace(event) {
  const button = event.target.closest("[data-inplace]");
  if (button && root()?.dataset.study) {
    event.preventDefault();
    startInplace(button);
  }
}

/* --- Мой регион своим цветом ----------------------------------------------------------- */

function markMine(scope = document) {
  const code = root()?.dataset.myRegion;
  if (!code) {
    return;
  }
  scope.querySelectorAll(`[data-territory="${CSS.escape(code)}"]`).forEach((node) => {
    node.classList.add(MINE_CLASS);
  });
}

/* --- Выбор ряда сайта: перечень грузится, когда окно открыто ---------------------------- */

function onPickerToggle(event) {
  const panel = event.target;
  if (panel.id !== "study-site-picker" || event.newState !== "open") {
    return;
  }
  const slot = panel.querySelector("[data-picker-slot]");
  const template = panel.querySelector("#study-site-picker-content");
  if (!slot || !template || slot.childElementCount) {
    return;
  }
  slot.append(template.content.cloneNode(true));
  import("../elements/combobox.js").catch(() => {});
}

/* --- Новая ссылка показывается сразу ------------------------------------------------------ */

function openNewShare() {
  const share = document.getElementById("study-share");
  if (share && share.querySelector(".own-share-new") && share.showPopover) {
    share.showPopover();
  }
}

/* --------------------------------------------------------------------------------------- */

export function init() {
  if (!root()) {
    return;
  }
  document.addEventListener("submit", onMenuSubmit);
  document.addEventListener("submit", onChangeSubmit);
  document.addEventListener("click", onExpand);
  document.addEventListener("click", onInplace);
  document.addEventListener("beforetoggle", onPickerToggle, true);
  document.addEventListener("fullscreenchange", refit);
  document.body.addEventListener("htmx:afterSwap", (event) => {
    markMine(event.target);
    bindSortable();
    bindExpand();
    settleNew();
  });
  // Заметка добавлена: поле очищается, форма сворачивается.
  document.body.addEventListener("htmx:afterRequest", (event) => {
    const form = event.target.closest?.(".study-add-note__form");
    if (form && event.detail.successful) {
      form.reset();
      form.closest("details")?.removeAttribute("open");
    }
  });
  markMine();
  bindExpand();
  openNewShare();
  if (window.Sortable) {
    bindSortable();
  } else {
    window.addEventListener("load", bindSortable, { once: true });
  }
}
