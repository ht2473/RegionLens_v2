/* Значения переменных tokens.css для графиков, рисуемых в canvas. */

let probe = null;

/** Получить действующий цвет переменной через пробный элемент. */
function resolveColour(name) {
  if (!probe) {
    probe = document.createElement("span");
    probe.setAttribute("aria-hidden", "true");
    probe.style.cssText = "position:absolute;width:0;height:0;overflow:hidden;opacity:0";
    document.body.appendChild(probe);
  }
  probe.style.color = "";
  probe.style.color = `var(${name})`;
  const resolved = getComputedStyle(probe).color;
  return resolved && resolved !== "rgba(0, 0, 0, 0)" ? resolved : "";
}

const SYSTEM_DARK = window.matchMedia("(prefers-color-scheme: dark)");

// Прочитанные значения — до смены темы: каждое чтение через пробный элемент заставляет
// браузер пересчитать стили, а в настройках графиков паспорта таких ссылок сотни.
const cache = new Map();
let cachedTheme = "";

/** Действующая тема: от неё зависят значения цветовых маркеров. */
function currentTheme() {
  return document.documentElement.getAttribute("data-theme") || (SYSTEM_DARK.matches ? "dark" : "light");
}

/**
 * Прочитать значение переменной оформления.
 *
 * light-dark() в переменной не раскрывается — цвет берётся через пробный элемент.
 */
export function token(name, fallback = "") {
  const theme = currentTheme();
  if (theme !== cachedTheme) {
    cache.clear();
    cachedTheme = theme;
  }
  if (!cache.has(name)) {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name);
    const text = value ? value.trim() : "";
    cache.set(name, text.includes("light-dark(") ? resolveColour(name) : text);
  }
  return cache.get(name) || fallback;
}

/**
 * Подставить действующие цвета вместо var(--token) в настройках; при каждой перерисовке.
 *
 * @param {*} value произвольный фрагмент настроек
 * @returns {*} тот же фрагмент с подставленными цветами
 */
export function resolveTokens(value) {
  if (typeof value === "string") {
    const match = /^var\((--[a-z0-9-]+)\)$/.exec(value);
    return match ? token(match[1], "") : value;
  }
  if (Array.isArray(value)) {
    return value.map(resolveTokens);
  }
  if (value && typeof value === "object") {
    const result = {};
    Object.keys(value).forEach((key) => {
      result[key] = resolveTokens(value[key]);
    });
    return result;
  }
  return value;
}

/** Высота закреплённой части экрана из маркеров — тех же, что у scroll-margin-top. */
export function stickyOffset() {
  const styles = getComputedStyle(document.documentElement);
  const size = (name, fallback) => parseFloat(styles.getPropertyValue(name)) || fallback;
  return size("--header-height", 64) + size("--breadcrumbs-height", 40);
}
