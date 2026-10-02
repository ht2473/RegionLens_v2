/* Картограмма рабочей поверхности <rl-choropleth>: разбор карты по классам легенды
   и сохранение чертежа файлом; карта приходит с сервера (apps/maps/cartogram.py). */

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

/** Отдать чертёж файлом. */
function save(map) {
  const blob = new Blob([serialize(map)], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = (document.title.split(" — ")[0] || "map") + ".svg";
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Освобождение ссылки на данные.
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
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

  /** Поставить кнопку сохранения в угол карты. */
  addSaveControl() {
    const map = this.querySelector("[data-geo-map]");
    const label = document.body.dataset.mapSave;
    if (!map || !label || this.querySelector(".geo-map-wrapper .chart-save")) {
      return;
    }

    const button = document.createElement("button");
    button.type = "button";
    button.className = "chart-save no-print";
    button.title = label;
    button.setAttribute("aria-label", label);
    button.innerHTML =
      '<svg aria-hidden="true"><use href="#icon-download"></use></svg><span>SVG</span>';
    button.addEventListener("click", () => save(map));

    map.parentNode.appendChild(button);
  }
}

if (!customElements.get("rl-choropleth")) {
  customElements.define("rl-choropleth", Choropleth);
}
