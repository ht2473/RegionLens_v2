/* Подсветка одного субъекта во всех представлениях по атрибуту data-territory;
   графики подписываются сами (follow) и отписываются, покидая страницу. */

// Класс подсветки — не класс выбора: подсветка живёт, пока указатель на месте.
const HIGHLIGHT_CLASS = "is-highlighted";

/** Код подсвеченной территории или null. */
let current = null;

/** Графики, линии которых обозначают территории: {instance, territories}. */
const followers = new Set();

/**
 * Передать подсветку графикам: соседние линии библиотека приглушает сама.
 *
 * @param {string|null} code код территории
 */
function paintCharts(code) {
  followers.forEach(({ instance, territories }) => {
    const chosen = Object.keys(territories).find((name) => territories[name] === code);
    // Снятие — первым и без указания ряда, иначе гасло бы приглушение остальных.
    instance.dispatchAction({ type: "downplay" });
    if (chosen) {
      instance.dispatchAction({ type: "highlight", seriesName: chosen });
    }
  });
}

/**
 * Подсветить территорию во всей разметке и на графиках.
 *
 * @param {string|null} code код территории
 * @param {boolean} silent не трогать графики: подсветка пришла от них самих
 */
export function set(code, silent = false) {
  const value = code || null;
  if (current === value) {
    return;
  }
  current = value;

  document.querySelectorAll("[data-territory]").forEach((node) => {
    node.classList.toggle(HIGHLIGHT_CLASS, value !== null && node.dataset.territory === value);
  });

  if (!silent) {
    paintCharts(value);
  }
}

/**
 * Связать график с подсветкой: линия под указателем отмечает строку и фигуру.
 *
 * @param {object} instance экземпляр графика
 * @param {object|null} territories соответствие названий линий кодам территорий
 * @returns {() => void} отписка — график вызывает её, покидая страницу
 */
export function follow(instance, territories) {
  if (!territories) {
    return () => {};
  }
  const entry = { instance, territories };
  followers.add(entry);
  instance.on("mouseover", (params) => {
    const code = territories[params.seriesName];
    if (code) {
      set(code, true);
    }
  });
  instance.on("mouseout", () => set(null, true));
  return () => followers.delete(entry);
}

/** Подключить обработчики наведения и фокуса на документе: холст заменяется фрагментами. */
export function init() {
  const track = (event) => {
    const node = event.target.closest ? event.target.closest("[data-territory]") : null;
    set(node ? node.dataset.territory : null);
  };
  document.addEventListener("mouseover", track);
  document.addEventListener("focusin", track);
  document.addEventListener("mouseleave", () => set(null));
}
