/* Картинки карт и графиков для материала и курсовой: PNG и SVG с заголовком, подзаголовком
   (год, единица) и строкой источника — без сервера. Подпись берётся из data-image-title,
   data-image-subtitle и data-image-source ближайшего предка; без них — из заголовка рамки
   графика и страницы. */

import { token } from "./tokens.js";

const SVG_NS = "http://www.w3.org/2000/svg";
// Поля, размеры шрифтов и межстрочные интервалы подписи, точки.
const PAD = 24;
const TITLE_SIZE = 20;
const SUBTITLE_SIZE = 14;
const SOURCE_SIZE = 12;
const LINE = 1.35;
// Средняя ширина знака в долях кегля — для переноса строк без замера.
const GLYPH = 0.56;
// Разрешение растровой картинки.
const PIXEL_RATIO = 2;

/**
 * Подпись картинки для элемента.
 *
 * @param {Element} element график или карта
 * @returns {{title: string, subtitle: string, source: string, site: string}}
 */
export function captionOf(element) {
  const host = element.closest("[data-image-title], [data-image-source]");
  const frame = element.closest(".chart-frame, .card, .indicator-chart");
  const frameTitle = frame
    ? (frame.querySelector(".chart-frame__title, .card__title, .indicator-chart__caption") || {})
        .textContent
    : "";
  const own = host ? host.dataset.imageTitle || "" : "";
  const title = clean(own || frameTitle || document.title.split(" — ")[0]);
  const parts = [];
  if (frameTitle && clean(frameTitle) !== title) {
    parts.push(clean(frameTitle));
  }
  if (host && host.dataset.imageSubtitle) {
    parts.push(clean(host.dataset.imageSubtitle));
  }
  return {
    title,
    subtitle: parts.filter(Boolean).join(" · "),
    source: clean(host ? host.dataset.imageSource || "" : ""),
    site: clean(document.body.dataset.siteName || "RegionLens"),
  };
}

function clean(text) {
  return String(text || "")
    .replace(/\s+/g, " ")
    .replace(/^[\s·]+|[\s·]+$/g, "")
    .trim();
}

/** Разбить текст на строки не шире ``width`` точек при кегле ``size``. */
function wrap(text, size, width) {
  if (!text) {
    return [];
  }
  const limit = Math.max(Math.floor(width / (size * GLYPH)), 12);
  const lines = [];
  let line = "";
  text.split(" ").forEach((word) => {
    const next = line ? `${line} ${word}` : word;
    if (next.length > limit && line) {
      lines.push(line);
      line = word;
    } else {
      line = next;
    }
  });
  if (line) {
    lines.push(line);
  }
  return lines;
}

/** Цвета и шрифт подписи — из темы страницы. */
function palette() {
  return {
    background: token("--surface-raised", "#ffffff"),
    primary: token("--text-primary", "#1f1a14"),
    secondary: token("--text-secondary", "#4a4239"),
    muted: token("--text-muted", "#6f665b"),
    font: window.getComputedStyle(document.body).fontFamily || "sans-serif",
  };
}

/**
 * Разметка подписи над и под картинкой: строки заголовка, подзаголовка и источника.
 *
 * @returns {{header: Array, footer: Array, headerHeight: number, footerHeight: number}}
 */
function layout(caption, width) {
  const inner = width - 2 * PAD;
  const header = [];
  let y = PAD;
  wrap(caption.title, TITLE_SIZE, inner).forEach((text) => {
    y += TITLE_SIZE * LINE;
    header.push({ text, y: y - TITLE_SIZE * (LINE - 1), size: TITLE_SIZE, weight: 600 });
  });
  wrap(caption.subtitle, SUBTITLE_SIZE, inner).forEach((text) => {
    y += SUBTITLE_SIZE * LINE;
    header.push({ text, y: y - SUBTITLE_SIZE * (LINE - 1), size: SUBTITLE_SIZE, tone: "secondary" });
  });
  const headerHeight = y + PAD / 2;
  const footer = [];
  let z = PAD / 2;
  const sourceLines = wrap(caption.source, SOURCE_SIZE, inner - caption.site.length * SOURCE_SIZE);
  (sourceLines.length ? sourceLines : [""]).forEach((text, index) => {
    z += SOURCE_SIZE * LINE;
    footer.push({ text, y: z, size: SOURCE_SIZE, tone: "muted", site: index === 0 });
  });
  return { header, footer, headerHeight, footerHeight: z + PAD };
}

function escapeXml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/**
 * Собрать самостоятельный SVG: подпись сверху, чертёж посередине, источник снизу.
 *
 * @param {string} drawing разметка SVG чертежа
 * @param {number} width ширина чертежа, точки
 * @param {number} height высота чертежа, точки
 * @param {object} caption подпись (captionOf)
 * @returns {{svg: string, width: number, height: number}}
 */
export function composeSvg(drawing, width, height, caption) {
  const colours = palette();
  const total = Math.max(width + 2 * PAD, 480);
  const plan = layout(caption, total);
  const fullHeight = Math.ceil(plan.headerHeight + height + plan.footerHeight);

  const parsed = new DOMParser().parseFromString(drawing, "image/svg+xml");
  const root = parsed.documentElement;
  root.setAttribute("x", String((total - width) / 2));
  root.setAttribute("y", String(plan.headerHeight));
  root.setAttribute("width", String(width));
  root.setAttribute("height", String(height));
  const body = new XMLSerializer().serializeToString(root);

  const tone = (line) => colours[line.tone || "primary"];
  const text = (line, x, anchor) =>
    `<text x="${x}" y="${line.y}" font-size="${line.size}"` +
    `${line.weight ? ` font-weight="${line.weight}"` : ""} fill="${tone(line)}"` +
    ` text-anchor="${anchor}">${escapeXml(line.text)}</text>`;
  const top = plan.header.map((line) => text(line, PAD, "start")).join("");
  const offset = plan.headerHeight + height;
  const bottom = plan.footer
    .map((line) => {
      const shifted = { ...line, y: line.y + offset };
      const site = line.site
        ? text({ ...shifted, text: caption.site, tone: "secondary" }, total - PAD, "end")
        : "";
      return text(shifted, PAD, "start") + site;
    })
    .join("");

  const svg =
    '<?xml version="1.0" encoding="UTF-8"?>\n' +
    `<svg xmlns="${SVG_NS}" xmlns:xlink="http://www.w3.org/1999/xlink" width="${total}"` +
    ` height="${fullHeight}" viewBox="0 0 ${total} ${fullHeight}"` +
    ` font-family="${escapeXml(colours.font)}">` +
    `<rect width="${total}" height="${fullHeight}" fill="${colours.background}"/>` +
    `${top}${body}${bottom}</svg>`;
  return { svg, width: total, height: fullHeight };
}

/** Перевести SVG в PNG двойного разрешения. */
export function svgToPng(svg, width, height) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml;charset=utf-8" }));
    const image = new Image();
    image.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(width * PIXEL_RATIO);
      canvas.height = Math.round(height * PIXEL_RATIO);
      const context = canvas.getContext("2d");
      context.scale(PIXEL_RATIO, PIXEL_RATIO);
      context.drawImage(image, 0, 0, width, height);
      URL.revokeObjectURL(url);
      resolve(canvas.toDataURL("image/png"));
    };
    image.onerror = (error) => {
      URL.revokeObjectURL(url);
      reject(error);
    };
    image.src = url;
  });
}

/**
 * Растровая картинка графика с подписью: картинка графика вписывается между строками.
 *
 * @param {string} chartUrl PNG графика (двойное разрешение)
 * @param {number} width ширина графика, точки
 * @param {number} height высота графика, точки
 * @param {object} caption подпись (captionOf)
 * @returns {Promise<string>} адрес данных PNG
 */
export function framePng(chartUrl, width, height, caption) {
  const placeholder = `<svg xmlns="${SVG_NS}" width="${width}" height="${height}"></svg>`;
  const composed = composeSvg(placeholder, width, height, caption);
  return svgToPng(composed.svg, composed.width, composed.height).then(
    (frame) =>
      new Promise((resolve, reject) => {
        const canvas = document.createElement("canvas");
        canvas.width = Math.round(composed.width * PIXEL_RATIO);
        canvas.height = Math.round(composed.height * PIXEL_RATIO);
        const context = canvas.getContext("2d");
        const base = new Image();
        base.onload = () => {
          context.drawImage(base, 0, 0);
          const chart = new Image();
          chart.onload = () => {
            const x = ((composed.width - width) / 2) * PIXEL_RATIO;
            const plan = layout(caption, composed.width);
            context.drawImage(
              chart,
              x,
              plan.headerHeight * PIXEL_RATIO,
              width * PIXEL_RATIO,
              height * PIXEL_RATIO,
            );
            resolve(canvas.toDataURL("image/png"));
          };
          chart.onerror = reject;
          chart.src = chartUrl;
        };
        base.onerror = reject;
        base.src = frame;
      }),
  );
}

/** Имя файла картинки: название системы, предмет и дата. */
export function fileName(caption, extension) {
  const subject = (caption.title || "chart")
    .replace(/[\\/:*?"<>|]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 80);
  const today = new Date().toISOString().slice(0, 10);
  return `${caption.site} — ${subject} — ${today}.${extension}`;
}

/** Отдать готовую картинку файлом. */
export function download(url, name) {
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  if (url.startsWith("blob:")) {
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}

/** Отдать SVG файлом. */
export function downloadSvg(svg, name) {
  download(URL.createObjectURL(new Blob([svg], { type: "image/svg+xml;charset=utf-8" })), name);
}

/**
 * Пара кнопок «PNG» и «SVG» для картинки.
 *
 * @param {object} labels подписи кнопок и их пояснения
 * @param {function(string): void} onSave обработчик: «png» или «svg»
 * @param {boolean} inline кнопки в строке заголовка рамки
 * @returns {HTMLElement}
 */
export function saveControls(labels, onSave, inline) {
  const group = document.createElement("div");
  group.className = inline ? "chart-save-group chart-save-group--inline no-print" : "chart-save-group no-print";
  group.setAttribute("role", "group");
  group.setAttribute("aria-label", labels.group);
  ["png", "svg"].forEach((kind) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "chart-save";
    button.title = labels[kind];
    button.setAttribute("aria-label", labels[kind]);
    button.innerHTML =
      '<svg aria-hidden="true"><use href="#icon-download"></use></svg>' +
      `<span>${kind.toUpperCase()}</span>`;
    button.addEventListener("click", () => onSave(kind));
    group.appendChild(button);
  });
  return group;
}

/** Подписи кнопок из разметки страницы. */
export function saveLabels() {
  const data = document.body.dataset;
  return {
    group: data.imageSave || "Сохранить картинкой",
    png: data.imageSavePng || "PNG",
    svg: data.imageSaveSvg || "SVG",
  };
}
