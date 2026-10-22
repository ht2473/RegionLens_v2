/* Отчёт Word со страницы исследования ([data-report]): к форме прикладываются картинки
   графиков и карт карточек (места — [data-report-image] в разметке карточки), файл
   приходит ответом и сохраняется. Картинки — в светлой теме: отчёт печатают на белом.
   Без сценария форма уходит как есть — отчёт без картинок. */

import { prepare } from "../lib/chart-options.js";
import { download, svgToPng } from "../lib/image-export.js";
import { token } from "../lib/tokens.js";

// Малые графики на картинке: ширина, столбцов, высота клетки и строки названия, точки.
const GRID_WIDTH = 960;
const GRID_COLUMNS = 4;
const CELL_HEIGHT = 64;
const CELL_GAP = 12;
const TITLE_HEIGHT = 28;
const TEXT_SIZE = 11;
// Разрешение растровых картинок — как у сохранения графика.
const PIXEL_RATIO = 2;

function escapeXml(text) {
  return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/**
 * Выполнить ``run`` в светлой теме и вернуть тему. Всё внутри — без ожидания: цвета
 * читаются сразу, страница не успевает перерисоваться.
 */
function inLightTheme(run) {
  const root = document.documentElement;
  const before = root.getAttribute("data-theme");
  root.setAttribute("data-theme", "light");
  try {
    return run();
  } finally {
    if (before === null) {
      root.removeAttribute("data-theme");
    } else {
      root.setAttribute("data-theme", before);
    }
  }
}

/** График заново во внеэкранном холсте: светлые цвета, без анимации. */
function chartPicture(chart) {
  if (!chart.instance || !window.echarts) {
    return null;
  }
  const width = chart.instance.getWidth();
  const height = chart.instance.getHeight();
  const holder = document.createElement("div");
  holder.style.cssText = `position:absolute;left:-10000px;top:0;width:${width}px;height:${height}px`;
  document.body.appendChild(holder);
  const copy = window.echarts.init(holder, null, { renderer: "canvas", width, height });
  copy.setOption({ ...prepare(chart.options, chart), animation: false });
  const url = copy.getDataURL({
    type: "png",
    pixelRatio: PIXEL_RATIO,
    backgroundColor: token("--surface-raised", "#ffffff"),
  });
  copy.dispose();
  holder.remove();
  return url;
}

/** Малые графики одной картинкой: группы по округам, клетка — название, линия, значение. */
function multiplesSvg(root) {
  const text = token("--text-primary", "#1f1a14");
  const muted = token("--text-muted", "#6f665b");
  const background = token("--surface-raised", "#ffffff");
  const accent = token("--accent", "#9d6d18");
  const font = (window.getComputedStyle(document.body).fontFamily || "sans-serif").replace(/"/g, "'");
  const cellWidth = (GRID_WIDTH - (GRID_COLUMNS - 1) * CELL_GAP) / GRID_COLUMNS;
  const limit = Math.floor(cellWidth / (TEXT_SIZE * 0.56));
  const parts = [];
  let y = 0;
  root.querySelectorAll(".multiples-group").forEach((group) => {
    const title = group.querySelector(".multiples-group__title")?.textContent.trim() || "";
    parts.push(
      `<text x="0" y="${y + 18}" font-size="13" font-weight="600" fill="${text}">${escapeXml(title)}</text>`,
    );
    y += TITLE_HEIGHT;
    [...group.querySelectorAll(".multiples-cell")].forEach((cell, index) => {
      const x = (index % GRID_COLUMNS) * (cellWidth + CELL_GAP);
      const top = y + Math.floor(index / GRID_COLUMNS) * CELL_HEIGHT;
      const name = cell.querySelector(".multiples-cell__name")?.firstChild?.textContent.trim() || "";
      const value = cell.querySelector(".multiples-cell__value")?.textContent.trim() || "";
      const line = cell.querySelector(".multiples-cell__line");
      const shown = name.length > limit ? `${name.slice(0, limit - 1)}…` : name;
      parts.push(
        `<text x="${x}" y="${top + 12}" font-size="${TEXT_SIZE}" fill="${muted}">${escapeXml(shown)}</text>`,
        `<text x="${x + cellWidth}" y="${top + CELL_HEIGHT - 10}" font-size="${TEXT_SIZE}"` +
          ` text-anchor="end" fill="${text}">${escapeXml(value)}</text>`,
      );
      if (line) {
        const box = line.getAttribute("viewBox");
        const paths = [...line.querySelectorAll("path")]
          .map((path) => {
            const dot = path.classList.contains("multiples-cell__dot");
            return (
              `<path d="${path.getAttribute("d")}" fill="none" stroke="${dot ? accent : text}"` +
              ` stroke-width="${dot ? 6 : 1.5}" stroke-linecap="round" stroke-linejoin="round"` +
              ' vector-effect="non-scaling-stroke"/>'
            );
          })
          .join("");
        parts.push(
          `<svg x="${x}" y="${top + 18}" width="${cellWidth - 64}" height="${CELL_HEIGHT - 26}"` +
            ` viewBox="${box}" preserveAspectRatio="none">${paths}</svg>`,
        );
      }
    });
    y += Math.ceil(group.querySelectorAll(".multiples-cell").length / GRID_COLUMNS) * CELL_HEIGHT + CELL_GAP;
  });
  const height = Math.max(y, 1);
  return {
    svg:
      `<svg xmlns="http://www.w3.org/2000/svg" width="${GRID_WIDTH}" height="${height}"` +
      ` viewBox="0 0 ${GRID_WIDTH} ${height}" font-family="${font}">` +
      `<rect width="${GRID_WIDTH}" height="${height}" fill="${background}"/>${parts.join("")}</svg>`,
    width: GRID_WIDTH,
    height,
  };
}

/**
 * Картинки всех карточек: поле формы → адрес данных PNG или обещание его. Графики сначала
 * строятся (нижние ещё не построены), затем всё снимается разом в светлой теме.
 */
async function pictures(choropleth) {
  const cards = [...document.querySelectorAll(".study-card[data-block]:not(.is-off-report)")];
  const charts = cards.flatMap((card) => [...card.querySelectorAll("rl-chart[data-report-image]")]);
  await Promise.all(charts.map((chart) => (typeof chart.mount === "function" ? chart.mount() : null)));
  return inLightTheme(() => {
    const found = [];
    cards.forEach((card) => {
      const block = card.dataset.block;
      card.querySelectorAll("[data-report-image]").forEach((place, index) => {
        let picture = null;
        if (place.localName === "rl-chart") {
          picture = chartPicture(place);
        } else if (place.localName === "rl-choropleth" && choropleth) {
          picture = choropleth.picture(place);
        } else if (place.classList.contains("multiples")) {
          const plan = multiplesSvg(place);
          picture = svgToPng(plan.svg, plan.width, plan.height);
        }
        if (picture) {
          found.push([`image-${block}-${index}`, picture]);
        }
      });
    });
    return found;
  });
}

/** Имя файла из заголовка ответа: имя в UTF-8, иначе обычное. */
function fileName(response, fallback) {
  const header = response.headers.get("Content-Disposition") || "";
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (encoded) {
    return decodeURIComponent(encoded[1]);
  }
  const plain = /filename="([^"]+)"/i.exec(header);
  return plain ? plain[1] : fallback;
}

async function onSubmit(event) {
  const form = event.target.closest("form[data-report]");
  if (!form || !window.fetch || !window.FormData) {
    return;
  }
  event.preventDefault();
  const submitter = event.submitter;
  const status = document.querySelector("[data-report-status]");
  const note = status ? status.textContent : "";
  const busy = form.dataset.busyLabel || "";
  if (submitter) {
    submitter.disabled = true;
  }
  if (status && busy) {
    status.textContent = busy;
  }
  try {
    const choropleth = document.querySelector("rl-choropleth")
      ? await import("../elements/choropleth.js").catch(() => null)
      : null;
    const data = new FormData(form);
    if (submitter && submitter.name) {
      data.set(submitter.name, submitter.value);
    }
    const found = await pictures(choropleth);
    const blobs = await Promise.all(
      found.map(([, picture]) =>
        Promise.resolve(picture)
          .then((url) => (url ? fetch(url).then((answer) => answer.blob()) : null))
          .catch(() => null),
      ),
    );
    found.forEach(([name], index) => {
      if (blobs[index]) {
        data.append(name, blobs[index], `${name}.png`);
      }
    });
    const response = await fetch(form.action, { method: "POST", body: data, credentials: "same-origin" });
    if (!response.ok) {
      throw new Error(await response.text());
    }
    const url = URL.createObjectURL(await response.blob());
    download(url, fileName(response, "regionlens-study.docx"));
    if (status) {
      status.textContent = note;
    }
  } catch (error) {
    // Без картинок отчёт всё равно нужен: форма уходит обычным путём.
    window.console.error("Отчёт с картинками не собрался", error);
    if (status) {
      status.textContent = note;
    }
    if (submitter) {
      submitter.disabled = false;
    }
    form.removeAttribute("data-report");
    form.requestSubmit(submitter || undefined);
  } finally {
    if (submitter) {
      submitter.disabled = false;
    }
  }
}

export function init() {
  if (document.body.dataset.reportReady === "true") {
    return;
  }
  document.body.dataset.reportReady = "true";
  document.addEventListener("submit", onSubmit);
}
