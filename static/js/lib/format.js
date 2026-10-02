/* Форматирование чисел для осей и подсказок графиков по правилам языка страницы. */

/** Пропуск в данных: пустое значение показывается прочерком, а не нулём. */
const missing = (value) => value === null || value === undefined || Number.isNaN(value);

/** Отформатировать число с заданным количеством знаков после запятой. */
export function number(value, digits = 1) {
  if (missing(value)) {
    return "—";
  }
  const locale = document.documentElement.lang === "en" ? "en-US" : "ru-RU";
  return new Intl.NumberFormat(locale, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
}

/** Отформатировать число, автоматически подобрав точность по его величине. */
export function auto(value) {
  if (missing(value)) {
    return "—";
  }
  const magnitude = Math.abs(value);
  if (magnitude >= 1000) {
    return number(value, 0);
  }
  if (magnitude >= 10) {
    return number(value, 1);
  }
  return number(value, 2);
}

/** Отформатировать деление оси; доли — только у дробного деления. */
export function axis(value) {
  if (!Number.isFinite(value)) {
    return "";
  }
  if (Number.isInteger(value)) {
    return number(value, 0);
  }
  return number(value, 2)
    .replace(/([,.]\d*?)0+$/, "$1")
    .replace(/[,.]$/, "");
}
