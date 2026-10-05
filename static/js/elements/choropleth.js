/* Картограмма рабочей поверхности <rl-choropleth>: разбор карты по классам легенды
   и сохранение картинкой PNG или SVG с легендой, заголовком и источником; карта приходит
   с сервера (apps/maps/cartogram.py). */

import {
  captionOf,
  composeSvg,
  download,
  downloadSvg,
  fileName,
  saveControls,
  saveLabels,
  svgToPng,
} from "../lib/image-export.js";
import { token } from "../lib/tokens.js";

/* --- Живая легенда: наведение приглушает остальные классы, щелчок оставляет выбранный -- */

// Класс, оставленный щелчком, — вне элемента: он переживает замену карты.
let locked = null;

/* --- Сохранение чертежа: цвета из переменных оформления проставляются в копию ------- */

// Свойства, переносимые в копию; всё оформление раздуло бы файл в сотни раз.
const PAINT = [
  "fill",
  "fill-opacity",
  "fill-rule",
  "stroke",
  "stroke-width",
  "stroke-opacity",
  "stroke-linejoin",
  "stroke-linecap",
  "stroke-dasharray",
  "opacity",
  "font-family",
  "font-size",
  "font-weight",
  "letter-spacing",
  "text-anchor",
  "dominant-baseline",
  "paint-order",
];

/**
 * Перенести на копию вычисленное оформление, отличающееся от родительского.
 *
 * @param {Element} origin элемент на странице
 * @param {Element} target его копия
 */
function inherit(origin, target) {
  const own = window.getComputedStyle(origin);
  const parent = origin.parentElement ? window.getComputedStyle(origin.parentElement) : null;

  PAINT.forEach((name) => {
    const value = own.getPropertyValue(name);
    // «none» переносится: рамка врезки задана fill: none.
    if (!value || value === "normal") {
      return;
    }
    if (parent && parent.getPropertyValue(name) === value) {
      return;
    }
    target.setAttribute(name, value);
  });
}

/**
 * Собрать самостоятельный чертёж из карты на странице.
 *
 * @param {SVGElement} source карта в документе
 * @returns {string} разметка SVG, годная для отдельного файла
 */
function serialize(source) {
  const copy = source.cloneNode(true);
  copy.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  copy.setAttribute("xmlns:xlink", "http://www.w3.org/1999/xlink");

  const origin = source.querySelectorAll("*");
  const target = copy.querySelectorAll("*");
  // Обход парами: порядок в копии повторяет исходный.
  for (let at = 0; at < origin.length; at += 1) {
    inherit(origin[at], target[at]);
    // Класс в отдельном файле не к чему приложить, а место занимает.
    target[at].removeAttribute("class");

    // Ссылка на паспорт — полным адресом.
    const href = target[at].getAttribute("href");
    if (target[at].tagName === "a" && href && href.charAt(0) === "/") {
      target[at].setAttribute("href", new URL(href, window.location.href).href);
    }
  }

  inherit(source, copy);
  copy.removeAttribute("class");

  // Правилами XML: outerHTML оставил бы &nbsp;, неизвестный XML.
  return '<?xml version="1.0" encoding="UTF-8"?>\n' + new XMLSerializer().serializeToString(copy);
}

// Ширина карты на картинке, точки; высота — по пропорциям чертежа.
const IMAGE_WIDTH = 960;
// Легенда под картой: образец цвета, отступы, кегль подписи.
const SWATCH = 14;
const LEGEND_GAP = 18;
const LEGEND_SIZE = 12;
const LEGEND_ROW = 24;

/**
 * Легенда картинки: классы шкалы и «нет данных» строками под картой.
 *
 * @param {Element} root элемент карты с легендой
 * @param {number} width ширина картинки
 * @returns {{markup: string, height: number}}
 */
function legendMarkup(root, width) {
  const items = [...root.querySelectorAll(".map-legend__class")].map((item) => {
    const swatch = item.querySelector(".map-legend__swatch");
    const range = item.querySelector(".map-legend__range");
    const style = swatch ? window.getComputedStyle(swatch) : null;
    return {
      colour: style ? style.backgroundColor : "transparent",
      border: style ? style.borderTopColor : "transparent",
      hatched: Boolean(swatch && swatch.classList.contains("map-legend__swatch--no-data")),
      label: range ? range.textContent.replace(/\s+/g, " ").trim() : "",
    };
  });
  if (!items.length) {
    return { markup: "", height: 0 };
  }
  const colour = token("--text-secondary", "#4a4239");
  const font = window.getComputedStyle(document.body).fontFamily || "sans-serif";
  const parts = [];
  let x = 0;
  let y = LEGEND_GAP;
  items.forEach((item) => {
    const itemWidth = SWATCH + 6 + item.label.length * LEGEND_SIZE * 0.56 + LEGEND_GAP;
    if (x > 0 && x + itemWidth > width) {
      x = 0;
      y += LEGEND_ROW;
    }
    const fill = item.hatched ? token("--no-data-fill", "#ddd") : item.colour;
    parts.push(
      `<rect x="${x}" y="${y}" width="${SWATCH}" height="${SWATCH}" rx="3" fill="${fill}"` +
        ` stroke="${item.border}"/>`,
      `<text x="${x + SWATCH + 6}" y="${y + SWATCH - 2}" font-size="${LEGEND_SIZE}"` +
        ` font-family="${font.replace(/"/g, "'")}" fill="${colour}">` +
        `${item.label.replace(/&/g, "&amp;").replace(/</g, "&lt;")}</text>`,
    );
    x += itemWidth;
  });
  return { markup: parts.join(""), height: y + SWATCH + LEGEND_GAP / 2 };
}

/**
 * Чертёж картинки: карта во всю ширину и легенда под ней.
 *
 * @param {Element} root элемент карты
 * @param {SVGElement} map карта в документе
 * @returns {{svg: string, width: number, height: number}}
 */
function drawing(root, map) {
  const box = map.viewBox && map.viewBox.baseVal;
  const ratio = box && box.width ? box.height / box.width : 0.6;
  const width = IMAGE_WIDTH;
  const mapHeight = Math.round(width * ratio);
  const legend = legendMarkup(root, width);
  const parsed = new DOMParser().parseFromString(serialize(map), "image/svg+xml").documentElement;
  parsed.setAttribute("width", String(width));
  parsed.setAttribute("height", String(mapHeight));
  const height = mapHeight + legend.height;
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"` +
    ` width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">` +
    new XMLSerializer().serializeToString(parsed) +
    `<g transform="translate(0 ${mapHeight})">${legend.markup}</g></svg>`;
  return { svg, width, height };
}

/** Сохранить карту картинкой с подписью: «png» или «svg». */
function save(root, map, kind) {
  const caption = captionOf(root);
  // Приглушение классов от наведения и щелчка в картинку не переносится; переходы
  // на время сохранения отключены, иначе стиль застал бы середину перехода.
  root.classList.add("is-exporting");
  root.apply(null);
  const plan = drawing(root, map);
  root.classList.remove("is-exporting");
  root.apply(locked);
  const composed = composeSvg(plan.svg, plan.width, plan.height, caption);
  if (kind === "svg") {
    downloadSvg(composed.svg, fileName(caption, "svg"));
    return;
  }
  svgToPng(composed.svg, composed.width, composed.height)
    .then((url) => download(url, fileName(caption, "png")))
    .catch(() => downloadSvg(composed.svg, fileName(caption, "svg")));
}

class Choropleth extends HTMLElement {
  connectedCallback() {
    if (!this.bound) {
      this.bound = true;
      this.bind();
    }
    this.onKeydown = (event) => {
      if (event.key === "Escape" && locked !== null) {
        locked = null;
        this.apply(null);
      }
    };
    document.addEventListener("keydown", this.onKeydown);
    this.addSaveControl();

    // Закреплённого класса может не быть в новой шкале.
    if (locked !== null && !this.regions().some((region) => region.dataset.class === locked)) {
      locked = null;
    }
    this.apply(locked);
  }

  disconnectedCallback() {
    document.removeEventListener("keydown", this.onKeydown);
  }

  /** Фигуры субъектов общей карты и врезок. */
  regions() {
    return [...this.querySelectorAll("[data-geo-map] [data-region]")];
  }

  /**
   * Приглушить все классы, кроме указанного.
   *
   * @param {string|null} value номер класса или null, чтобы снять приглушение
   */
  apply(value) {
    this.regions().forEach((region) => {
      region.classList.toggle("is-muted", value !== null && region.dataset.class !== value);
    });
    this.querySelectorAll("[data-legend-class]").forEach((control) => {
      const active = value !== null && control.dataset.legendClass === value;
      control.classList.toggle("is-active", active);
      control.setAttribute("aria-pressed", control.dataset.legendClass === locked ? "true" : "false");
    });
  }

  bind() {
    this.addEventListener("mouseover", (event) => {
      if (locked !== null) {
        return;
      }
      const control = event.target.closest("[data-legend-class]");
      if (control) {
        this.apply(control.dataset.legendClass);
        return;
      }
      const region = event.target.closest("[data-geo-map] [data-region]");
      if (region && region.dataset.class) {
        this.apply(region.dataset.class);
      }
    });

    this.addEventListener("mouseout", (event) => {
      const inside =
        event.target.closest("[data-legend-class]") ||
        event.target.closest("[data-geo-map] [data-region]");
      if (inside && locked === null) {
        this.apply(null);
      }
    });

    this.addEventListener("click", (event) => {
      const control = event.target.closest("[data-legend-class]");
      if (!control) {
        return;
      }
      const value = control.dataset.legendClass;
      locked = locked === value ? null : value;
      this.apply(locked);
    });
  }

  /** Поставить кнопки сохранения картинкой PNG и SVG в угол карты. */
  addSaveControl() {
    const map = this.querySelector("[data-geo-map], svg.tile-map, svg.multiples");
    if (!map || !document.body.dataset.imageSave || map.parentNode.querySelector(".chart-save")) {
      return;
    }
    map.parentNode.appendChild(saveControls(saveLabels(), (kind) => save(this, map, kind), false));
  }
}

if (!customElements.get("rl-choropleth")) {
  customElements.define("rl-choropleth", Choropleth);
}
