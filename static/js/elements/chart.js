/* График <rl-chart data-options="<id>"> по настройкам из {{ option|json_script:"<id>" }}.
   Строится, когда подходит к окну (и во фрагменте HTMX): библиотека не грузится ради графиков
   ниже первого экрана. Освобождается, когда уносят; перед печатью строятся все. */

import {
  describe,
  describeWords,
  fixTextMeasure,
  isNarrow,
  prepare,
} from "../lib/chart-options.js";
import { follow } from "../lib/highlight.js";
import {
  captionOf,
  composeSvg,
  download,
  downloadSvg,
  fileName,
  framePng,
  saveControls,
  saveLabels,
} from "../lib/image-export.js";
import { onThemeChange } from "../lib/theme.js";
import { token } from "../lib/tokens.js";

// Прозрачность линии и её подписи, когда выделена другая линия.
const DIMMED_LINE = 0.15;
const DIMMED_LABEL = 0.3;

/** Графики, стоящие сейчас в документе: их перерисовывают смена темы и размера окна. */
const mounted = new Set();

/** Ждущие подхода к окну; без IntersectionObserver график строится сразу. */
const waiting = new Set();
const watcher =
  "IntersectionObserver" in window
    ? new IntersectionObserver(
        (entries) => {
          entries.forEach((entry) => {
            if (entry.isIntersecting) {
              watcher.unobserve(entry.target);
              waiting.delete(entry.target);
              entry.target.mount();
            }
          });
        },
        // С запасом в полэкрана: график готов, когда до него докрутили.
        { rootMargin: "50% 0px" },
      )
    : null;

/* --- Библиотека: подключена тегом или догружается по адресу из body ----------------- */

let library = null;

/** Дождаться библиотеки графиков; при необходимости — запросить её. */
function loadLibrary() {
  if (window.echarts) {
    return Promise.resolve(window.echarts);
  }
  if (!library) {
    library = new Promise((resolve, reject) => {
      let script = document.querySelector('script[data-chart-library], script[src*="echarts"]');
      if (!script) {
        const source = document.body.dataset.chartLibrary;
        if (!source) {
          reject(new Error("Адрес библиотеки графиков не объявлен"));
          return;
        }
        script = document.createElement("script");
        script.src = source;
        script.dataset.chartLibrary = "lazy";
        document.head.appendChild(script);
      }
      // Тег страницы выполняется после входного модуля: библиотеки может ещё не быть.
      script.addEventListener("load", () => resolve(window.echarts), { once: true });
      script.addEventListener("error", reject, { once: true });
    });
  }
  return library;
}

/* --- Узкое начертание: библиотека измеряет подписи в canvas один раз, поэтому
   начертание загружается до построения. -------------------------------------------- */

let fontRequested = false;

function ensureFont() {
  if (fontRequested || !document.fonts || !document.fonts.load) {
    return;
  }
  fontRequested = true;
  const family = token("--font-narrow", "");
  if (!family) {
    return;
  }
  // Образец с кириллицей: поднаборы загружаются порознь.
  document.fonts
    .load(`400 12px ${family}`, "Аа Aa 0")
    .then(() => mounted.forEach((chart) => chart.redraw()))
    .catch(() => {});
}

/* --------------------------------------------------------------------------------- */

/**
 * Настройки с названиями шрифтов в одинарных кавычках: строковый вывод SVG библиотеки
 * вписывает семейство в атрибут как есть, и «"Segoe UI"» ломает разметку файла.
 *
 * @param {*} value настройки или их часть
 * @returns {*} копия с исправленными fontFamily
 */
function singleQuotedFonts(value) {
  if (Array.isArray(value)) {
    return value.map(singleQuotedFonts);
  }
  if (!value || typeof value !== "object") {
    return value;
  }
  const copy = {};
  Object.entries(value).forEach(([key, item]) => {
    copy[key] =
      key === "fontFamily" && typeof item === "string"
        ? item.replace(/"/g, "'")
        : singleQuotedFonts(item);
  });
  return copy;
}

/** Настройки графика из {{ option|json_script }}; нет или не разобрать — null. */
function readOptions(element) {
  const source = document.getElementById(element.dataset.options);
  if (!source) {
    return null;
  }
  try {
    return JSON.parse(source.textContent);
  } catch (error) {
    window.console.error("Не удалось разобрать настройки графика", element.dataset.options);
    return null;
  }
}

class RegionChart extends HTMLElement {
  connectedCallback() {
    // Настройки после элемента могут быть ещё не вставлены — построение откладывается.
    queueMicrotask(() => {
      // Текстовая замена — сразу, а не при подходе к окну: программа чтения идёт по тексту.
      const options = this.isConnected ? readOptions(this) : null;
      if (options) {
        this.describe(options);
      }
      if (watcher && this.isConnected && !this.instance) {
        waiting.add(this);
        watcher.observe(this);
      } else {
        this.mount();
      }
    });
  }

  disconnectedCallback() {
    mounted.delete(this);
    // Унесли один график, а не фрагмент целиком, — его описание уносится вместе с ним.
    if (this.description && !this.isConnected) {
      this.description.remove();
      this.description = null;
    }
    if (watcher) {
      watcher.unobserve(this);
      waiting.delete(this);
    }
    if (this.saveControl && !this.saveControl.isConnected) {
      this.saveControl = null;
    }
    if (this.unfollow) {
      this.unfollow();
      this.unfollow = null;
    }
    if (this.instance) {
      this.instance.dispose();
      this.instance = null;
    }
  }

  /**
   * Поставить перед графиком скрытый абзац с подписью и описанием по данным (один раз).
   * Не роль «img» на самом элементе: без строки заголовка рамки кнопки сохранения
   * встают внутрь него и стали бы недоступны программе чтения.
   */
  describe(options) {
    if (this.description && this.description.isConnected) {
      return;
    }
    const caption = captionOf(this);
    const head = [caption.title, caption.subtitle].filter(Boolean).join(", ");
    const body = describe(options);
    const paragraph = document.createElement("p");
    paragraph.className = "visually-hidden";
    paragraph.textContent = [
      `${describeWords().chart}: ${head}.`,
      body.length ? `${body.join("; ")}.` : "",
    ]
      .filter(Boolean)
      .join(" ");
    this.before(paragraph);
    this.description = paragraph;
  }

  /** Построить график, если он ещё не построен и стоит в документе. */
  async mount() {
    if (this.instance || this.mounting || !this.isConnected) {
      return;
    }
    const options = readOptions(this);
    if (!options) {
      return;
    }

    this.mounting = true;
    const echarts = await loadLibrary().catch(() => null);
    this.mounting = false;
    // Пока шла библиотека, график могли унести новым фрагментом.
    if (!echarts || !this.isConnected || this.instance) {
      return;
    }

    ensureFont();
    fixTextMeasure(echarts);

    this.options = options;
    this.narrow = isNarrow(this);
    // Настройки — до создания: полоса точек задаёт холсту высоту по своей раскладке.
    const prepared = prepare(options, this);
    this.instance = echarts.init(this, null, { renderer: "canvas" });
    this.instance.setOption(prepared);
    this.enableFocus(prepared);
    this.enablePick();
    this.addSaveControl();
    // Соответствие «название линии — код территории»; у мер неравенства пусто.
    this.unfollow = follow(this.instance, options.territories || null);
    mounted.add(this);
  }

  /** Собрать график заново из исходных настроек, сняв выделение линии. */
  redraw() {
    if (!this.instance) {
      return;
    }
    this.pinned = null;
    this.narrow = isNarrow(this);
    this.instance.setOption(prepare(this.options, this), true);
    // Полоса точек могла сменить высоту холста.
    this.instance.resize();
  }

  /**
   * Подогнать размер под контейнер; при смене раскладки подписей — собрать заново.
   * Полоса точек раскладывается по ширине — собирается заново всегда.
   */
  fit() {
    if (!this.instance) {
      return;
    }
    if (isNarrow(this) !== this.narrow || this.options.swarm) {
      this.redraw();
    }
    this.instance.resize();
  }

  /**
   * Выбор щелчком по элементу графика (служебный раздел pick: {x, y} — селекторы полей формы):
   * у элемента данных pick — значения полей; поле получает значение, форма — событие change.
   * То же без мыши — ссылками рядом с графиком.
   */
  enablePick() {
    const pick = this.options.pick;
    if (!pick) {
      return;
    }
    this.instance.on("click", (params) => {
      const values = params.data && params.data.pick;
      if (!values) {
        return;
      }
      const fields = [pick.x, pick.y].map((selector) => document.querySelector(selector));
      if (fields.some((field) => !field)) {
        return;
      }
      fields.forEach((field, index) => {
        field.value = values[index];
      });
      fields[0].dispatchEvent(new Event("change", { bubbles: true }));
    });
  }

  /**
   * Разрешить выделение одной линии щелчком до следующего щелчка.
   *
   * Приглушение — прозрачностью: «размытие» библиотеки не отзывается на выделение из кода.
   */
  enableFocus(prepared) {
    // Исходная прозрачность — чтобы вернуть окружение приглушённым; ряды — уже разложенные.
    const initial = (prepared.series || []).map((entry) => ({
      name: entry.name,
      focusable: Boolean(entry.triggerLineEvent),
      line: entry.lineStyle && entry.lineStyle.opacity !== undefined ? entry.lineStyle.opacity : 1,
      label: entry.endLabel && entry.endLabel.opacity !== undefined ? entry.endLabel.opacity : 1,
    }));

    if (initial.filter((entry) => entry.focusable).length < 2) {
      return;
    }

    this.pinned = null;
    const apply = () => {
      this.instance.setOption({
        series: initial.map((entry) => {
          if (!entry.focusable) {
            return {};
          }
          if (this.pinned === null) {
            return { lineStyle: { opacity: entry.line }, endLabel: { opacity: entry.label } };
          }
          const chosen = entry.name === this.pinned;
          return {
            lineStyle: { opacity: chosen ? 1 : DIMMED_LINE },
            endLabel: { opacity: chosen ? 1 : DIMMED_LABEL },
          };
        }),
      });
    };

    this.instance.on("click", (params) => {
      if (params.componentType !== "series") {
        return;
      }
      this.pinned = this.pinned === params.seriesName ? null : params.seriesName;
      apply();
    });

    // Щелчок мимо линий снимает выделение.
    this.instance.getZr().on("click", (event) => {
      if (!event.target && this.pinned !== null) {
        this.pinned = null;
        apply();
      }
    });
  }

  /**
   * Поставить кнопки сохранения картинкой: PNG (двойное разрешение) и SVG, обе — с
   * заголовком, годом и единицей сверху и строкой источника снизу.
   *
   * Кнопки — в строке заголовка рамки графика, без неё — в углу холста; подписи — из разметки.
   */
  addSaveControl() {
    if (!document.body.dataset.imageSave || this.saveControl) {
      return;
    }
    const frame = this.closest(".chart-frame, .card, .indicator-chart");
    const row = frame
      ? frame.querySelector(".chart-frame__header, .card__header, .indicator-chart__caption")
      : null;
    const group = saveControls(saveLabels(), (kind) => this.saveImage(kind), Boolean(row));
    (row || this).appendChild(group);
    this.saveControl = group;
  }

  /** Сохранить график картинкой с подписью: «png» или «svg». */
  saveImage(kind) {
    if (!this.instance) {
      return;
    }
    const caption = captionOf(this);
    const width = this.instance.getWidth();
    const height = this.instance.getHeight();
    if (kind === "svg") {
      // Чертёж — отдельным экземпляром с векторной отрисовкой в невидимом узле, без анимации.
      const holder = document.createElement("div");
      holder.style.cssText = `position:absolute;left:-10000px;top:0;width:${width}px;height:${height}px`;
      document.body.appendChild(holder);
      const vector = window.echarts.init(holder, null, { renderer: "svg", width, height });
      vector.setOption({ ...singleQuotedFonts(prepare(this.options, this)), animation: false });
      const drawing = vector.renderToSVGString();
      vector.dispose();
      holder.remove();
      if (drawing) {
        downloadSvg(composeSvg(drawing, width, height, caption).svg, fileName(caption, "svg"));
      }
      return;
    }
    // Подложка явно: холст прозрачен.
    const chart = this.instance.getDataURL({
      type: "png",
      pixelRatio: 2,
      backgroundColor: token("--surface-raised", "#ffffff"),
    });
    framePng(chart, width, height, caption)
      .then((url) => download(url, fileName(caption, "png")))
      .catch(() => download(chart, fileName(caption, "png")));
  }
}

// Печать: строятся и те, до которых не докрутили.
window.addEventListener("beforeprint", () => {
  [...waiting].forEach((chart) => {
    watcher.unobserve(chart);
    waiting.delete(chart);
    chart.mount();
  });
});

// Перерисовка после смены темы — с задержкой до пересчёта стилей.
onThemeChange(() => window.setTimeout(() => mounted.forEach((chart) => chart.redraw()), 0));

let resizeTimer = null;
window.addEventListener("resize", () => {
  window.clearTimeout(resizeTimer);
  resizeTimer = window.setTimeout(() => mounted.forEach((chart) => chart.fit()), 150);
});

if (!customElements.get("rl-chart")) {
  customElements.define("rl-chart", RegionChart);
}
