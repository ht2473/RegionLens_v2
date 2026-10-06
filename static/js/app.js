/* Входной ES-модуль без сборки: подключает службы lib/ и поведения шапки; остальные
   поведения ui/ и пользовательские элементы elements/ загружаются, когда их признак
   появляется в документе. */

import * as announce from "./lib/announce.js";
import * as highlight from "./lib/highlight.js";
import * as theme from "./lib/theme.js";
import * as dialogs from "./ui/dialogs.js";
import * as fold from "./ui/fold.js";
import * as palette from "./ui/palette.js";
import * as popovers from "./ui/popovers.js";

/* --- Пользовательские элементы: адреса строками — так их переписывает хранилище статики -- */

const ELEMENTS = {
  "rl-chart": () => import("./elements/chart.js"),
  "rl-choropleth": () => import("./elements/choropleth.js"),
  "rl-combobox": () => import("./elements/combobox.js"),
  "rl-live-map": () => import("./elements/live-map.js"),
  "rl-time-slider": () => import("./elements/time-slider.js"),
};

/** Загрузить определения элементов, которые стоят в документе, но ещё не определены. */
function defineElements() {
  Object.entries(ELEMENTS).forEach(([tag, load]) => {
    if (!customElements.get(tag) && document.querySelector(tag)) {
      load().catch((error) => window.console.error(`Не удалось загрузить ${tag}`, error));
    }
  });
}

/* --- Поведения по признаку в разметке. fold.js — в шапке модуля: он сворачивает блоки
   при загрузке, и запоздавший модуль дал бы скачок раскрытого блока. ------------------- */

const BEHAVIOURS = {
  clipboard: ["[data-copy], [data-select-all]", () => import("./ui/clipboard.js")],
  forms: [
    "[data-count-of], [data-filter-input], [data-summary-of], form[data-autosubmit]",
    () => import("./ui/forms.js"),
  ],
  formula: ["[data-formula-insert]", () => import("./ui/formula.js")],
  more: ["[data-more-toggle]", () => import("./ui/more.js")],
  print: ["[data-print]", () => import("./ui/print.js")],
  scroll: [
    ".data-table-wrapper, .admin-table-wrapper, .edition-list, .admin-log, .code-block",
    () => import("./ui/scroll.js"),
  ],
  sheet: ["[data-rail-open]", () => import("./ui/sheet.js")],
  toc: ["[data-toc]", () => import("./ui/toc.js")],
  upload: [
    "form[data-upload-form], textarea.textarea--table, [data-error-summary]",
    () => import("./ui/upload.js"),
  ],
};

// Загруженные поведения: имя → обещание модуля с выполненным init().
const loaded = new Map();

/** Загрузить поведения, признак которых есть в документе. */
function loadBehaviours() {
  Object.entries(BEHAVIOURS).forEach(([name, [selector, load]]) => {
    if (loaded.has(name) || !document.querySelector(selector)) {
      return;
    }
    const module = load()
      .then((behaviour) => {
        behaviour.init();
        return behaviour;
      })
      .catch((error) => window.console.error(`Не удалось загрузить ${name}`, error));
    loaded.set(name, module);
  });
}

/**
 * Обновить уже загруженное поведение для пришедшего фрагмента.
 *
 * @param {string} name имя поведения
 * @param {(behaviour: object) => void} callback что сделать с модулем
 */
function refresh(name, callback) {
  loaded.get(name)?.then((behaviour) => behaviour && callback(behaviour));
}

/* --------------------------------------------------------------------------------- */

theme.init();
highlight.init();
dialogs.init();
palette.init();
popovers.init();
fold.init();
loadBehaviours();
defineElements();

// Пересчитываемая область — aria-busy.
document.body.addEventListener("htmx:beforeRequest", (event) => {
  const canvas = event.detail.target;
  if (canvas && canvas.classList && canvas.classList.contains("canvas")) {
    canvas.setAttribute("aria-busy", "true");
  }
});

document.body.addEventListener("htmx:afterRequest", (event) => {
  const canvas = event.detail.target;
  if (canvas && canvas.removeAttribute) {
    canvas.removeAttribute("aria-busy");
  }
});

// Пришедший фрагмент: элементы, поведения, формы, счётчики и состояние листа рейля.
document.body.addEventListener("htmx:afterSwap", (event) => {
  defineElements();
  refresh("forms", (forms) => {
    forms.enhance(event.target);
    forms.refreshCounters();
  });
  refresh("more", (more) => more.enhance(event.target));
  refresh("scroll", (scroll) => scroll.enhance(event.target));
  refresh("sheet", (sheet) => sheet.reflect());
  loadBehaviours();
  fold.enhance(event.target);
  announce.fromSwap(event.target);
});
