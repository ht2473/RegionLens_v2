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

// Полоса точек: поля холста по горизонтали (прикидка: ось значений библиотека ставит сама),
// высота подписей оси под полем и наименьшая высота холста.
const SWARM_MARGIN = 56;
const SWARM_AXIS = 44;
const SWARM_MIN_HEIGHT = 140;

/**
 * Разложить точки полосы без перекрытий (служебный раздел swarm): точки с близкими
 * значениями сдвигаются вверх и вниз — ближайший свободный сдвиг, считая от середины.
 * Сдвиг считается в диаметрах точки по настоящей ширине холста, высота холста — под полосу.
 *
 * @param {object} prepared настройки, уже приведённые к теме
 * @param {HTMLElement} element холст
 * @returns {object} те же настройки со сдвигами точек
 */
function swarm(prepared, element) {
  const spec = prepared.swarm;
  delete prepared.swarm;
  const series = spec && prepared.series ? prepared.series[spec.series] : null;
  if (!series || !element || !element.clientWidth) {
    return prepared;
  }
  // Диаметр с зазором, точек.
  const size = (series.symbolSize || 8) + 2;
  const data = series.data || [];
  const values = data.map((item) => item.value[0]);
  const low = Math.min(...values);
  const span = Math.max(...values) - low || 1;
  const width = Math.max(element.clientWidth - SWARM_MARGIN, 120) / size;
  const across = values.map((value) => ((value - low) / span) * width);

  const placed = [];
  across
    .map((_, index) => index)
    .sort((a, b) => across[a] - across[b])
    .forEach((index) => {
      const x = across[index];
      const near = placed.filter((point) => Math.abs(point.x - x) < 1);
      const candidates = [0];
      near.forEach((point) => {
        const rise = Math.sqrt(1 - (point.x - x) ** 2);
        candidates.push(point.y + rise, point.y - rise);
      });
      candidates.sort((a, b) => Math.abs(a) - Math.abs(b));
      const y =
        candidates.find((c) => near.every((point) => (point.x - x) ** 2 + (point.y - c) ** 2 > 0.999)) ?? 0;
      placed.push({ x, y });
      data[index].value = [values[index], y];
    });

  const reach = Math.max(1, ...placed.map((point) => Math.abs(point.y))) + 0.8;
  prepared.yAxis = Object.assign({}, prepared.yAxis, { min: -reach, max: reach });
  const grid = prepared.grid || {};
  const height = 2 * reach * size + (grid.top || 0) + (grid.bottom || 0) + SWARM_AXIS;
  element.style.height = `${Math.max(Math.ceil(height), SWARM_MIN_HEIGHT)}px`;
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

  // Общее оформление рядов, помеченных seriesTemplate, — одним образцом с сервера.
  if (prepared.seriesTemplate) {
    const template = prepared.seriesTemplate;
    prepared.series = (prepared.series || []).map((entry) => {
      if (!entry || !entry.seriesTemplate) {
        return entry;
      }
      const own = Object.assign({}, entry);
      delete own.seriesTemplate;
      return merge(template, own);
    });
    delete prepared.seriesTemplate;
  }

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

  // Соответствие линий территориям и поля выбора щелчком библиотеке не передаются.
  delete prepared.territories;
  delete prepared.pick;

  unscroll(prepared, element);
  swarm(prepared, element);
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

/* --- Текстовая замена: описание графика по настройкам -------------------------------
   Холст программы чтения с экрана не читают. Короткая подпись — заголовок графика,
   длинное описание — по данным (W3C WAI, «Complex images»): у линий и столбцов — первое
   и последнее значение, у полосы точек — крайние, у облака точек — их число и оси. */

// Линий, о которых говорится поимённо; об остальных — только их число.
const NAMED_LINES = 6;
// Столбцов одного ряда без названия (вклад округов), перечисляемых поимённо.
const NAMED_BARS = 12;

/** Слова описания — из разметки страницы, на её языке. */
export function describeWords() {
  const data = document.body.dataset;
  return {
    chart: data.chartLabel || "График",
    lines: data.chartLines || "Линий всего",
    points: data.chartPoints || "Точек",
    total: data.chartTotal || "Всего значений",
    lowest: data.chartLowest || "наименьшее",
    highest: data.chartHighest || "наибольшее",
    across: data.chartAcross || "по горизонтали",
    up: data.chartUp || "по вертикали",
  };
}

/** Число описания: малые доли («вклад 0,0012») — с четырьмя знаками, иначе «0,00». */
function figure(value) {
  if (value === 0) {
    return format.number(0, 0);
  }
  return Math.abs(value) < 0.1 ? format.number(value, 4) : format.auto(value);
}

/** Значение точки: число, {value}, [x, y] или {value: [x, y]}; ``index`` — координата пары. */
function valueOf(item, index = 0) {
  const raw = item !== null && typeof item === "object" && !Array.isArray(item) ? item.value : item;
  const value = Array.isArray(raw) ? raw[index] : raw;
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function asList(value) {
  if (Array.isArray(value)) {
    return value;
  }
  return value ? [value] : [];
}

/** Ось категорий (годы, названия) — та, у которой есть подписи делений. */
function categories(options) {
  const axes = [...asList(options.xAxis), ...asList(options.yAxis)];
  const found = axes.find((axis) => axis && axis.type === "category" && Array.isArray(axis.data));
  return found ? found.data.map((item) => (item && typeof item === "object" ? item.value : item)) : null;
}

/** Ряды, которые видит человек: служебные (коридор, подложка) — без названия или «тихие». */
function visibleSeries(options) {
  return asList(options.series).filter(
    (series) => series && series.name && !series.silent && Array.isArray(series.data),
  );
}

function describeLines(series, labels, text) {
  const parts = [];
  series.slice(0, NAMED_LINES).forEach((line) => {
    const filled = line.data
      .map((item, index) => [index, valueOf(item)])
      .filter(([, value]) => value !== null);
    if (!filled.length) {
      return;
    }
    const [firstIndex, first] = filled[0];
    const [lastIndex, last] = filled[filled.length - 1];
    const start = `${labels[firstIndex] ?? ""} — ${figure(first)}`.trim();
    parts.push(
      firstIndex === lastIndex
        ? `${line.name}: ${start}`
        : `${line.name}: ${start}, ${labels[lastIndex] ?? ""} — ${figure(last)}`,
    );
  });
  if (series.length > NAMED_LINES) {
    parts.push(`${text.lines}: ${series.length}`);
  }
  return parts;
}

function describePoints(options, series, text) {
  const points = series.data.filter((item) => valueOf(item, 0) !== null);
  if (!points.length) {
    return [];
  }
  const parts = [`${text.points}: ${points.length}`];
  const vertical = asList(options.yAxis)[0] || {};
  if (vertical.show === false) {
    // Полоса точек: значение — по горизонтали; крайние — поимённо.
    const sorted = [...points].sort((a, b) => valueOf(a, 0) - valueOf(b, 0));
    const name = (item) => (item && item.name ? ` (${item.name})` : "");
    const low = sorted[0];
    const high = sorted[sorted.length - 1];
    parts.push(`${text.lowest} — ${figure(valueOf(low, 0))}${name(low)}`);
    parts.push(`${text.highest} — ${figure(valueOf(high, 0))}${name(high)}`);
    return parts;
  }
  const across = (asList(options.xAxis)[0] || {}).name;
  if (across) {
    parts.push(`${text.across} — ${across}`);
  }
  if (vertical.name) {
    parts.push(`${text.up} — ${vertical.name}`);
  }
  return parts;
}

function describeBars(bars, labels, text) {
  const parts = bars.data
    .map((item, index) => [labels[index], valueOf(item)])
    .filter(([label, value]) => label !== undefined && value !== null)
    .map(([label, value]) => `${label} — ${figure(value)}`);
  if (parts.length > NAMED_BARS) {
    return [...parts.slice(0, NAMED_BARS), `${text.total}: ${parts.length}`];
  }
  return parts;
}

/** Описание графика по его настройкам; без понятных данных — только подпись. */
export function describe(options) {
  const text = describeWords();
  const series = visibleSeries(options);
  const labels = categories(options);
  // Полоса и облако точек бывают и без названия ряда.
  const scatter = asList(options.series).find(
    (item) => item && item.type === "scatter" && !item.silent && Array.isArray(item.data),
  );
  if (scatter) {
    return describePoints(options, scatter, text);
  }
  if (!labels) {
    return [];
  }
  const lines = series.filter((item) => item.type === "line" || item.type === "bar");
  if (lines.length) {
    return describeLines(lines, labels, text);
  }
  // Один ряд столбцов без названия: каждая категория — со своим значением.
  const bars = asList(options.series).filter(
    (item) => item && item.type === "bar" && Array.isArray(item.data),
  );
  return bars.length === 1 ? describeBars(bars[0], labels, text) : [];
}
