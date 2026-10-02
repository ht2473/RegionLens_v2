/* График <rl-chart data-options="<id>"> по настройкам из {{ option|json_script:"<id>" }}.
   Строится, когда появляется в документе (и во фрагменте HTMX), освобождается, когда уносят. */

import { fixTextMeasure, isNarrow, prepare } from "../lib/chart-options.js";
import { follow } from "../lib/highlight.js";
import { onThemeChange } from "../lib/theme.js";
import { token } from "../lib/tokens.js";

// Прозрачность линии и её подписи, когда выделена другая линия.
const DIMMED_LINE = 0.15;
const DIMMED_LABEL = 0.3;

/** Графики, стоящие сейчас в документе: их перерисовывают смена темы и размера окна. */
const mounted = new Set();

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

/** Имя файла картинки: название системы, предмет графика и дата. */
function fileName(title) {
  // Название системы в конце заголовка окна отсекается.
  const page = document.title.split(" — ")[0];
  const subject = (title || page || "chart")
    .replace(/[\\/:*?"<>|]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 80);
  const today = new Date().toISOString().slice(0, 10);
  return `RegionLens — ${subject} — ${today}.png`;
}

/** Отдать готовую картинку файлом. */
function download(url, name) {
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

class RegionChart extends HTMLElement {
  connectedCallback() {
    // Настройки после элемента могут быть ещё не вставлены — построение откладывается.
    queueMicrotask(() => this.mount());
  }

  disconnectedCallback() {
    mounted.delete(this);
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

  /** Построить график, если он ещё не построен и стоит в документе. */
  async mount() {
    if (this.instance || this.mounting || !this.isConnected) {
      return;
    }
    const source = document.getElementById(this.dataset.options);
    if (!source) {
      return;
    }
    let options;
    try {
      options = JSON.parse(source.textContent);
    } catch (error) {
      window.console.error("Не удалось разобрать настройки графика", this.dataset.options);
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
    this.instance = echarts.init(this, null, { renderer: "canvas" });
    this.narrow = isNarrow(this);
    this.instance.setOption(prepare(options, this));
    this.enableFocus();
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
  }

  /** Подогнать размер под контейнер; при смене раскладки подписей — собрать заново. */
  fit() {
    if (!this.instance) {
      return;
    }
    if (isNarrow(this) !== this.narrow) {
      this.redraw();
    }
    this.instance.resize();
  }

  /**
   * Разрешить выделение одной линии щелчком до следующего щелчка.
   *
   * Приглушение — прозрачностью: «размытие» библиотеки не отзывается на выделение из кода.
   */
  enableFocus() {
    // Исходная прозрачность — чтобы вернуть окружение приглушённым.
    const initial = (this.options.series || []).map((entry) => ({
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
   * Поставить кнопку сохранения картинки (двойное разрешение, подложка карточки).
   *
   * Кнопка — в строке заголовка рамки графика, без неё — в углу холста; подпись — из разметки.
   */
  addSaveControl() {
    const label = document.body.dataset.chartSave;
    if (!label || this.saveControl) {
      return;
    }
    const frame = this.closest(".chart-frame, .card, .indicator-chart");
    const row = frame
      ? frame.querySelector(".chart-frame__header, .card__header, .indicator-chart__caption")
      : null;

    const button = document.createElement("button");
    button.type = "button";
    button.className = row ? "chart-save chart-save--inline no-print" : "chart-save no-print";
    button.title = label;
    button.innerHTML =
      '<svg aria-hidden="true"><use href="#icon-download"></use></svg>' +
      `<span>${document.body.dataset.chartSaveShort || "PNG"}</span>`;
    button.setAttribute("aria-label", label);

    button.addEventListener("click", () => {
      if (!this.instance) {
        return;
      }
      // Подложка явно: холст прозрачен.
      const url = this.instance.getDataURL({
        type: "png",
        pixelRatio: 2,
        backgroundColor: token("--surface-raised", "#ffffff"),
      });
      const title = this.options.title && this.options.title.text ? this.options.title.text : "";
      download(url, fileName(title));
    });

    (row || this).appendChild(button);
    this.saveControl = button;
  }
}

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
