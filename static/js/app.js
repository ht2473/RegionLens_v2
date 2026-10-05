/* Входной ES-модуль без сборки: подключает службы lib/, поведения ui/ и загружает
   пользовательские элементы elements/, когда они появляются в документе. */

import * as announce from "./lib/announce.js";
import * as highlight from "./lib/highlight.js";
import * as theme from "./lib/theme.js";
import * as clipboard from "./ui/clipboard.js";
import * as dialogs from "./ui/dialogs.js";
import * as fold from "./ui/fold.js";
import * as forms from "./ui/forms.js";
import * as formula from "./ui/formula.js";
import * as more from "./ui/more.js";
import * as palette from "./ui/palette.js";
import * as popovers from "./ui/popovers.js";
import * as scroll from "./ui/scroll.js";
import * as sheet from "./ui/sheet.js";
import * as toc from "./ui/toc.js";
import * as upload from "./ui/upload.js";

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

/* --------------------------------------------------------------------------------- */

theme.init();
highlight.init();
clipboard.init();
dialogs.init();
palette.init();
popovers.init();
sheet.init();
toc.init();
forms.init();
fold.init();
more.init();
scroll.init();
upload.init();
formula.init();
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

// Пришедший фрагмент: элементы, формы, счётчики и состояние листа рейля.
document.body.addEventListener("htmx:afterSwap", (event) => {
  defineElements();
  forms.enhance(event.target);
  forms.refreshCounters();
  fold.enhance(event.target);
  more.enhance(event.target);
  scroll.enhance(event.target);
  sheet.reflect();
  announce.fromSwap(event.target);
});
