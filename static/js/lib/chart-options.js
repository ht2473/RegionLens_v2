/* Настройки графиков в браузере: цвета темы, запись чисел языка и раскладка под ширину холста. */

import * as format from "./format.js";
import { resolveTokens, token } from "./tokens.js";

// Ширина холста, ниже которой подписи у концов линий заменяет легенда.
const NARROW_CHART = 520;

// Высота одной строки легенды под полем графика и поле над ней.
const LEGEND_ROW = 20;
const LEGEND_PAD = 8;

/** Собрать базовые настройки оформления графика. */
function baseOptions() {
  return {
    // Подписи — узким начертанием, подсказка — основным.
    textStyle: {
      fontFamily: token("--font-narrow", "Onest, sans-serif"),
      fontSize: 12,
      color: token("--text-secondary", "#4a5a70"),
    },
    grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
    color: [1, 2, 3, 4, 5, 6, 7, 8].map((index) => token(`--series-${index}`)),
    tooltip: {
      backgroundColor: token("--surface-overlay", "#ffffff"),
      borderColor: token("--border-subtle", "#e1e7f0"),
      borderWidth: 1,
      padding: [8, 12],
      // Подсказка удерживается внутри поля графика.
      confine: true,
      // Длинные названия рядов переносятся.
      extraCssText:
        "max-width: 320px; white-space: normal;" +
        " box-shadow: 0 4px 12px rgba(14, 23, 38, 0.1); border-radius: 6px;",
      textStyle: {
        fontFamily: token("--font-sans", "Onest, sans-serif"),
        color: token("--text-primary", "#0e1726"),
        fontSize: 12,
      },
    },
    animationDuration: 320,
    animationEasing: "cubicOut",
  };
}

/** Настройки оси значений с сеткой слабого контраста. */
function valueAxis() {
  return {
    type: "value",
    axisLine: { show: false },
    axisTick: { show: false },
    splitLine: {
      lineStyle: { color: token("--border-subtle", "#e1e7f0"), type: "dashed" },
    },
    axisLabel: { color: token("--text-muted", "#7a8aa0") },
  };
}

/** Настройки оси категорий. */
function categoryAxis(data) {
  return {
    type: "category",
    data: data,
    axisLine: { lineStyle: { color: token("--border-strong", "#c7d2e1") } },
    axisTick: { show: false },
    axisLabel: { color: token("--text-muted", "#7a8aa0") },
  };
}

/**
 * Соединить базовые настройки с серверными: словари — вглубь, списки — целиком.
 *
 * @param {object} base базовые настройки оформления
 * @param {object} extra настройки, пришедшие с сервера
 * @returns {object} соединённые настройки
 */
function merge(base, extra) {
  const isPlain = (value) => value !== null && typeof value === "object" && !Array.isArray(value);

  if (!isPlain(base) || !isPlain(extra)) {
    return extra === undefined ? base : extra;
  }

  const result = Object.assign({}, base);
  Object.keys(extra).forEach((key) => {
    result[key] =
      isPlain(base[key]) && isPlain(extra[key]) ? merge(base[key], extra[key]) : extra[key];
  });
  return result;
}

/**
 * Прикинуть по длине названий, во сколько строк уложится легенда: место под неё
 * библиотека не резервирует.
 *
 * @param {string[]} names названия рядов
 * @param {number} width ширина холста
 * @param {number} [cap] наибольшая ширина подписи одного ряда
 * @returns {number} число строк
 */
function legendRows(names, width, cap = 150) {
  // Значок с отступом до подписи, промежуток между рядами и средняя ширина
  // знака подписи кеглем 12.
  const ICON = 22;
  const GAP = 14;
  const GLYPH = 6.6;

  let rows = 1;
  let line = 0;
  names.forEach((name) => {
    const item = ICON + Math.min(name.length * GLYPH, cap) + GAP;
    if (line > 0 && line + item > width) {
      rows += 1;
      line = item;
    } else {
      line += item;
    }
  });
  return rows;
}

/**
 * Переложить график под узкий холст: подписи у концов линий — в легенду снизу.
 *
 * @param {object} prepared настройки, уже приведённые к теме
 * @param {HTMLElement} element холст
 * @returns {object} те же настройки, переложенные под узкий холст
 */
function reflow(prepared, element) {
  const series = prepared.series || [];
  // В легенде — только ряды, подписанные у конца: без служебных рядов коридора.
  const named = series
    .filter((entry) => entry && entry.endLabel && entry.endLabel.show)
    .map((entry) => {
      entry.endLabel = Object.assign({}, entry.endLabel, { show: false });
      return entry.name;
    });

  if (!named.length) {
    return prepared;
  }

  const rows = legendRows(named, element.clientWidth);

  prepared.legend = merge(prepared.legend || {}, {
    show: true,
    data: named,
    // Легенда переносится по строкам, а не листается.
    type: "plain",
    top: "auto",
    bottom: 0,
    // Легенда — ключ к цветам, не переключатель рядов.
    selectedMode: false,
    textStyle: { width: 150, overflow: "truncate" },
  });

  const grid = prepared.grid || {};
  prepared.grid = Object.assign({}, grid, {
    right: 16,
    bottom: (typeof grid.bottom === "number" ? grid.bottom : 8) + LEGEND_PAD + rows * LEGEND_ROW,
  });

  return prepared;
}

// Наибольшая ширина подписи ряда в легенде над графиком (как textStyle.width у сервера).
const LEGEND_LABEL = 240;

/**
 * Развернуть листаемую легенду в строки и опустить поле графика на их высоту.
 *
 * @param {object} prepared настройки, уже приведённые к теме
 * @param {HTMLElement} element холст
 * @returns {object} те же настройки с легендой в строки
 */
function unscroll(prepared, element) {
  const legend = prepared.legend;
  if (!legend || legend.show === false || legend.type !== "scroll" || !element || !element.clientWidth) {
    return prepared;
  }
  const names = (legend.data || (prepared.series || []).map((entry) => entry && entry.name))
    .map((entry) => (entry && typeof entry === "object" ? entry.name : entry))
    .filter(Boolean);
  const rows = legendRows(names, element.clientWidth, LEGEND_LABEL);
  prepared.legend = Object.assign({}, legend, { type: "plain" });
  const grid = prepared.grid;
  if (rows > 1 && grid && !Array.isArray(grid) && typeof grid.top === "number") {
    prepared.grid = Object.assign({}, grid, { top: grid.top + (rows - 1) * LEGEND_ROW });
  }
  return prepared;
}

/**
 * Покрасить текст компонентов маркерами темы: легенду, названия осей и подписи шкалы
 * ECharts рисует своим серым, в тёмной теме он почти сливается с карточкой.
 *
 * @param {object} prepared настройки, уже приведённые к теме
 * @returns {object} те же настройки с цветами текста
 */
function paintText(prepared) {
  const text = token("--text-secondary", "#534d46");
  const muted = token("--text-muted", "#635b52");
  const faint = token("--border-strong", "#d5c9b6");
  const each = (value, paint) => (Array.isArray(value) ? value.map(paint) : paint(value));

  if (prepared.legend) {
    prepared.legend = each(prepared.legend, (legend) =>
      merge(
        {
          textStyle: { color: text },
          inactiveColor: faint,
          pageTextStyle: { color: muted },
          pageIconColor: text,
          pageIconInactiveColor: faint,
        },
        legend,
      ),
    );
  }
  ["xAxis", "yAxis"].forEach((name) => {
    if (prepared[name]) {
      prepared[name] = each(prepared[name], (axis) =>
        axis ? merge({ nameTextStyle: { color: muted }, axisLabel: { color: muted } }, axis) : axis,
      );
    }
  });
  if (prepared.visualMap) {
    prepared.visualMap = each(prepared.visualMap, (scale) => merge({ textStyle: { color: text } }, scale));
  }
  return prepared;
}

/** Тесно ли холсту для подписей у концов линий. */
export function isNarrow(element) {
  return Boolean(element) && element.clientWidth > 0 && element.clientWidth < NARROW_CHART;
}

/**
 * Подготовить настройки к отрисовке: цвета темы и запись чисел по языку страницы.
 *
 * @param {object} options настройки, пришедшие с сервера
 * @param {HTMLElement} [element] контейнер: по его ширине выбирается раскладка
 * @returns {object} настройки, готовые к передаче библиотеке
 */
export function prepare(options, element) {
  const prepared = merge(baseOptions(), resolveTokens(options));

  // Оси — здесь: цвета из переменных темы, разряды — по языку страницы.
  ["xAxis", "yAxis"].forEach((name) => {
    const single = !Array.isArray(prepared[name]);
    const axes = single ? [prepared[name]] : prepared[name];
    const styled = axes.map((axis) => {
      if (!axis) {
        return axis;
      }
      if (axis.type === "value") {
        const labelled = merge(axis, { axisLabel: { formatter: (value) => format.axis(value) } });
        return merge(valueAxis(), labelled);
      }
      if (axis.type === "category") {
        return merge(categoryAxis(axis.data), axis);
      }
      return axis;
    });
    prepared[name] = single ? styled[0] : styled;
  });

  if (prepared.tooltip && prepared.tooltip.valueFormatter === null) {
    prepared.tooltip.valueFormatter = (value) => format.auto(value);
  }

  // Соответствие линий территориям библиотеке не передаётся.
  delete prepared.territories;

  unscroll(prepared, element);
  return paintText(isNarrow(element) ? reflow(prepared, element) : prepared);
}

let measureFixed = false;

/**
 * Поправить оценку ширины букв при переносе подписей.
 *
 * ECharts 6 оценивает любую нелатинскую букву шириной «国»; образцом берётся русская «н».
 *
 * @param {object} echarts библиотека графиков
 */
export function fixTextMeasure(echarts) {
  if (measureFixed || typeof echarts.setPlatformAPI !== "function") {
    return;
  }
  measureFixed = true;
  const context = document.createElement("canvas").getContext("2d");
  let currentFont = "";
  echarts.setPlatformAPI({
    measureText(text, font) {
      const wanted = font || "12px sans-serif";
      if (wanted !== currentFont) {
        context.font = wanted;
        currentFont = wanted;
      }
      return context.measureText(text === "国" ? "н" : text);
    },
  });
}
