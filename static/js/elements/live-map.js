/* Живая карта <rl-live-map>: карточки региона, перебор годов, поиск и перечни крайних
   поверх серверной карты. Всё показанное приходит готовым в кадрах (apps/core/showcase.py);
   ячейки data-live необязательны. */

// Задержка между кадрами воспроизведения.
const FRAME_DELAY = 800;

class LiveMap extends HTMLElement {
  connectedCallback() {
    const source = document.getElementById(this.dataset.state);
    if (!source) {
      return;
    }
    this.state = JSON.parse(source.textContent);
    this.frames = { [String(this.state.year)]: this.state.frame };
    this.year = this.state.year;
    this.hovered = null;
    this.marked = null;
    this.timer = null;
    this.loading = null;

    // Фигуры субъекта: одна на общей карте и ещё одна во врезке у малых регионов.
    this.index = new Map(this.state.codes.map((code, position) => [code, position]));
    this.shapes = new Map();
    this.querySelectorAll("[data-geo-map] [data-region]").forEach((shape) => {
      const code = shape.dataset.region;
      if (!this.shapes.has(code)) {
        this.shapes.set(code, []);
      }
      this.shapes.get(code).push(shape);
    });

    this.slots = {};
    this.querySelectorAll("[data-live]").forEach((node) => {
      this.slots[node.dataset.live] = node;
    });
    this.panel = document.getElementById(this.dataset.cardPanel);
    this.emptyText = this.slots["card-empty"].textContent;

    this.bind();
    this.revealChoice();
    this.fitBounds();
    this.onResize = () => this.fitBounds();
    window.addEventListener("resize", this.onResize);
  }

  disconnectedCallback() {
    this.stop();
    window.removeEventListener("resize", this.onResize);
    if (this.panelHandler && this.panel) {
      this.panel.removeEventListener("toggle", this.panelHandler);
    }
  }

  /** Подключить обработчики наведения, щелчка, ползунка и поиска. */
  bind() {
    const map = this.querySelector("[data-geo-map]");
    if (map) {
      map.addEventListener("pointerover", (event) => {
        const code = this.codeAt(event.target);
        if (code) {
          this.showCard(code);
        }
      });
      map.addEventListener("pointerleave", () => this.showCard(null));
      map.addEventListener("focusin", (event) => {
        const code = this.codeAt(event.target);
        if (code) {
          this.showCard(code);
        }
      });
      map.addEventListener("click", (event) => {
        const code = this.codeAt(event.target);
        // Щелчок с модификатором — обычная ссылка.
        if (!code || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey) {
          return;
        }
        event.preventDefault();
        this.openCard(code);
      });
    }

    const range = this.slots.range;
    if (range) {
      range.addEventListener("input", () => {
        this.stop();
        this.setYear(Number(range.value));
      });
      // Кадры забираются при первом касании ползунка.
      range.addEventListener("pointerdown", () => this.load(), { once: true });
      range.addEventListener("focus", () => this.load(), { once: true });
    }

    if (this.slots.play) {
      this.slots.play.addEventListener("click", () => {
        if (this.timer !== null) {
          this.stop();
        } else {
          this.play();
        }
      });
    }

    const find = this.slots.find;
    if (find) {
      find.addEventListener("change", () => {
        const code = this.codeByName(find.value);
        if (code) {
          this.openCard(code);
        }
      });
      find.form.addEventListener("submit", (event) => {
        const code = this.codeByName(find.value);
        if (code) {
          event.preventDefault();
          this.openCard(code);
        }
      });
    }

    if (this.panel) {
      // Закрытие карточки любым способом снимает отметку.
      this.panelHandler = (event) => {
        if (event.newState === "closed") {
          this.mark(null);
        }
      };
      this.panel.addEventListener("toggle", this.panelHandler);
    }

    // Обработчики — на перечнях: строки переписываются при смене года.
    [this.slots.top, this.slots.bottom].forEach((list) => {
      if (!list) {
        return;
      }
      list.addEventListener("pointerover", (event) => this.pointAt(this.codeInList(event.target)));
      list.addEventListener("focusin", (event) => this.pointAt(this.codeInList(event.target)));
      list.addEventListener("pointerleave", () => this.pointAt(null));
      list.addEventListener("focusout", () => this.pointAt(null));
      list.addEventListener("click", (event) => {
        const code = this.codeInList(event.target);
        if (!code || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey) {
          return;
        }
        event.preventDefault();
        this.openCard(code);
      });
    });
  }

  /** Код региона строки перечня крайних. */
  codeInList(node) {
    const link = node && node.closest ? node.closest("[data-code]") : null;
    return link ? link.dataset.code : null;
  }

  /**
   * Показать регион строки перечня на карте и в карточке под указателем;
   * без строки — вернуть отметку региона, чья карточка открыта.
   *
   * @param {string|null} code код субъекта
   */
  pointAt(code) {
    this.highlight(code !== null ? code : this.marked);
    this.showCard(code);
  }

  /** Прокрутить полосу выбора к выбранному показателю, не двигая страницу. */
  revealChoice() {
    const chosen = this.querySelector('.choice-chip[aria-current="true"]');
    const strip = chosen ? chosen.closest(".choice-list") : null;
    if (!strip || strip.scrollWidth <= strip.clientWidth) {
      return;
    }
    const item = chosen.parentElement;
    const start = item.offsetLeft - strip.offsetLeft;
    const end = start + item.offsetWidth;
    if (end > strip.scrollLeft + strip.clientWidth || start < strip.scrollLeft) {
      strip.scrollLeft = Math.max(0, end - strip.clientWidth + item.offsetWidth / 2);
    }
  }

  /** Код субъекта, к фигуре которого относится элемент. */
  codeAt(node) {
    const shape = node && node.closest ? node.closest("[data-region]") : null;
    return shape ? shape.dataset.region : null;
  }

  /** Код субъекта по названию, введённому в поиск; регистр не важен. */
  codeByName(value) {
    const wanted = value.trim().toLocaleLowerCase();
    if (!wanted) {
      return null;
    }
    const position = this.state.names.findIndex((name) => name.toLocaleLowerCase() === wanted);
    return position >= 0 ? this.state.codes[position] : null;
  }

  /* --- Карточка под указателем ------------------------------------------------------ */

  /**
   * Показать в карточке субъект или подсказку, если указателя на карте нет.
   *
   * @param {string|null} code код субъекта
   */
  showCard(code) {
    this.hovered = code;
    const frame = this.frames[String(this.year)];
    const position = code === null ? -1 : this.index.get(code);
    const known = frame && position !== undefined && position >= 0 && frame.values[position];

    this.slots["card-empty"].hidden = Boolean(known);
    this.slots["card-body"].hidden = !known;
    if (!known) {
      this.slots["card-empty"].textContent =
        code !== null && position !== undefined && position >= 0
          ? `${this.state.names[position]} — ${this.dataset.labelMissing}`
          : this.emptyText;
      return;
    }
    this.slots["card-name"].textContent = this.state.names[position];
    this.slots["card-value"].textContent = frame.values[position];
    this.slots["card-place"].textContent = frame.places[position];
    this.slots["card-standing"].textContent = frame.standings[position];
    this.slots["card-standing"].className = `verdict verdict--${frame.tones[position] || "neutral"}`;
    this.slots["card-versus"].textContent = frame.versus[position];
    this.slots["card-versus"].hidden = !frame.versus[position];
  }

  /* --- Карточка региона ------------------------------------------------------------- */

  /**
   * Открыть короткий паспорт региона во всплывающей панели.
   *
   * @param {string} code код субъекта
   */
  async openCard(code) {
    const position = this.index.get(code);
    if (position === undefined || !this.panel) {
      return;
    }
    this.mark(code);
    this.showCard(code);
    const body = this.panel.querySelector("[data-region-card-body]");
    const url = `${this.state.cards[position]}?series=${encodeURIComponent(this.state.series)}`;
    body.setAttribute("aria-busy", "true");
    try {
      const response = await fetch(url, { headers: { Accept: "text/html" } });
      if (!response.ok) {
        throw new Error(String(response.status));
      }
      body.innerHTML = await response.text();
    } catch (error) {
      // Карточка не пришла — переход в паспорт.
      window.location.href = this.shapes.get(code)[0].getAttribute("href");
      return;
    } finally {
      body.removeAttribute("aria-busy");
    }
    if (!this.panel.matches(":popover-open")) {
      this.panel.showPopover();
    }
    this.mark(code);
    const heading = body.querySelector("h2");
    if (heading) {
      heading.setAttribute("tabindex", "-1");
      heading.focus({ preventScroll: true });
    }
  }

  /**
   * Отметить субъект на карте обводкой или снять отметку.
   *
   * @param {string|null} code код субъекта
   */
  mark(code) {
    this.marked = code;
    this.highlight(code);
  }

  /**
   * Обвести субъект на карте, не меняя отметки открытой карточки.
   *
   * @param {string|null} code код субъекта
   */
  highlight(code) {
    this.querySelectorAll("[data-region].is-highlighted").forEach((shape) => {
      shape.classList.remove("is-highlighted");
    });
    if (code !== null) {
      (this.shapes.get(code) || []).forEach((shape) => shape.classList.add("is-highlighted"));
    }
  }

  /* --- Годы ------------------------------------------------------------------------- */

  /** Забрать кадры всех годов; повторный вызов ждёт тот же ответ. */
  load() {
    if (!this.loading) {
      this.loading = fetch(this.state.framesUrl, { headers: { Accept: "application/json" } })
        .then((response) => {
          if (!response.ok) {
            throw new Error(String(response.status));
          }
          return response.json();
        })
        .then((payload) => {
          Object.assign(this.frames, payload.frames);
        })
        .catch(() => {
          // Кадры не пришли — повтор при следующем касании.
          this.loading = null;
        });
    }
    return this.loading;
  }

  /**
   * Показать карту за год.
   *
   * @param {number} year год
   */
  async setYear(year) {
    this.year = year;
    this.write("year-output", String(year));
    if (!this.frames[String(year)]) {
      await this.load();
    }
    // Рисуется последний выбранный год.
    if (this.year === year && this.frames[String(year)]) {
      this.paint(this.frames[String(year)]);
    }
  }

  /**
   * Перекрасить карту и переписать подписи по кадру года.
   *
   * @param {object} frame кадр, собранный сервером
   */
  paint(frame) {
    const unit = this.state.unit;
    this.state.codes.forEach((code, position) => {
      const index = frame.classes[position];
      const fill = index === null || index === undefined ? this.state.noData : `var(${frame.palette[index]})`;
      const value = frame.values[position];
      const title = value ? `${this.state.names[position]}: ${value} ${unit}` : this.state.names[position];
      (this.shapes.get(code) || []).forEach((shape) => {
        shape.dataset.class = index === null || index === undefined ? "" : String(index);
        const use = shape.querySelector("use");
        if (use) {
          use.setAttribute("fill", fill);
        }
        const label = shape.querySelector("title");
        if (label) {
          label.textContent = title;
        }
      });
    });

    this.write("year", String(frame.year));
    this.write("year-output", String(frame.year));
    if (this.slots.range) {
      this.slots.range.value = String(frame.year);
    }
    if (frame.highest) {
      this.write("highest-name", frame.highest.name);
      this.write("highest-value", frame.highest.value);
    }
    if (frame.lowest) {
      this.write("lowest-name", frame.lowest.name);
      this.write("lowest-value", frame.lowest.value);
    }
    this.write("country", frame.country);
    this.reveal("country-line", Boolean(frame.country));
    this.write("missing-count", String(frame.missing));
    this.reveal("missing", Boolean(frame.missing));
    this.paintLegend(frame.legend);
    this.paintLeaders(this.slots.top, frame, frame.top);
    this.paintLeaders(this.slots.bottom, frame, frame.bottom);

    if (this.hovered !== null) {
      this.showCard(this.hovered);
    }
    this.remember(frame.year);
  }

  /**
   * Записать текст в ячейку, если она есть в разметке.
   *
   * @param {string} name имя ячейки (data-live)
   * @param {string} text текст
   */
  write(name, text) {
    if (this.slots[name]) {
      this.slots[name].textContent = text;
    }
  }

  /**
   * Показать или скрыть ячейку, если она есть в разметке.
   *
   * @param {string} name имя ячейки (data-live)
   * @param {boolean} visible показывать ли
   */
  reveal(name, visible) {
    if (this.slots[name]) {
      this.slots[name].hidden = !visible;
    }
  }

  /**
   * Переписать перечень крайних регионов по кадру года (как catalog/partials/_leader_row.html).
   *
   * @param {HTMLElement|undefined} list перечень
   * @param {object} frame кадр
   * @param {Array<[number, string]>|undefined} rows номера регионов и длины полосок
   */
  paintLeaders(list, frame, rows) {
    if (!list || !rows) {
      return;
    }
    const items = rows.map(([position, share]) => {
      const item = document.createElement("li");
      item.className = "leader-list__item";
      const link = document.createElement("a");
      link.className = "leader-list__name";
      link.href = this.state.links[position];
      link.dataset.code = this.state.codes[position];
      link.textContent = this.state.names[position];
      const value = document.createElement("span");
      value.className = "leader-list__value numeric";
      value.textContent = frame.values[position];
      item.append(link, value);
      if (share) {
        const bar = document.createElement("span");
        bar.className = "leader-list__bar";
        bar.setAttribute("aria-hidden", "true");
        bar.style.setProperty("--share", share);
        const index = frame.classes[position];
        if (index !== null && index !== undefined) {
          bar.style.setProperty("--bar", `var(${frame.palette[index]})`);
        }
        item.append(bar);
      }
      return item;
    });
    list.replaceChildren(...items);
  }

  /**
   * Переписать легенду по кадру года (как core/partials/_live_legend.html).
   *
   * @param {Array<{colour: string, range: string, lower: string, upper: string}>} legend классы
   */
  paintLegend(legend) {
    const list = this.slots.legend;
    if (list) {
      list.replaceChildren(
        ...legend.map((entry) => {
          const item = document.createElement("li");
          item.className = "live-legend__item";
          item.title = entry.range;
          const swatch = document.createElement("span");
          swatch.className = "live-legend__swatch";
          swatch.style.backgroundColor = `var(${entry.colour})`;
          item.append(swatch);
          return item;
        })
      );
    }

    const bounds = this.slots.bounds;
    if (bounds && legend.length) {
      const bound = (text, share) => {
        const item = document.createElement("li");
        item.style.setProperty("--at", `${Math.round(share * 100)}%`);
        item.textContent = text;
        return item;
      };
      bounds.replaceChildren(
        bound(legend[0].lower, 0),
        ...legend.map((entry, index) => bound(entry.upper, (index + 1) / legend.length))
      );
      this.fitBounds();
    }
  }

  /** Скрыть внутренние границы, наезжающие на соседние; крайние остаются. */
  fitBounds() {
    const bounds = this.slots.bounds;
    if (!bounds) {
      return;
    }
    const items = [...bounds.children];
    items.forEach((item) => {
      item.style.visibility = "";
    });
    const GAP = 12;
    const last = items[items.length - 1];
    let edge = -Infinity;
    items.forEach((item, index) => {
      const box = item.getBoundingClientRect();
      const clashesEnd = item !== last && last && box.right + GAP > last.getBoundingClientRect().left;
      if (index > 0 && item !== last && (box.left < edge + GAP || clashesEnd)) {
        item.style.visibility = "hidden";
        return;
      }
      edge = box.right;
    });
  }

  /**
   * Записать год в адрес заменой текущей записи истории.
   *
   * @param {number} year год
   */
  remember(year) {
    const url = new URL(window.location.href);
    url.searchParams.set("series", this.state.series);
    if (year === this.state.years[this.state.years.length - 1]) {
      url.searchParams.delete("year");
    } else {
      url.searchParams.set("year", String(year));
    }
    window.history.replaceState(window.history.state, "", url);
  }

  /** Запустить перебор годов; с последнего года он начинается сначала. */
  async play() {
    const years = this.state.years;
    this.setPlaying(true);
    await this.load();
    // Воспроизведение могли остановить, пока шли кадры.
    if (!this.playing) {
      return;
    }
    let position = years.indexOf(this.year);
    if (position < 0 || position >= years.length - 1) {
      position = -1;
    }
    const step = () => {
      position += 1;
      this.setYear(years[position]);
      if (position >= years.length - 1) {
        this.stop();
      }
    };
    step();
    if (this.playing) {
      this.timer = window.setInterval(step, FRAME_DELAY);
    }
  }

  /** Остановить перебор годов. */
  stop() {
    if (this.timer !== null) {
      window.clearInterval(this.timer);
      this.timer = null;
    }
    this.setPlaying(false);
  }

  /**
   * Отметить кнопку воспроизведения: знак, подпись и состояние для программ чтения.
   *
   * @param {boolean} playing идёт ли перебор
   */
  setPlaying(playing) {
    this.playing = playing;
    const button = this.slots.play;
    if (!button) {
      return;
    }
    button.setAttribute("aria-pressed", playing ? "true" : "false");
    this.slots["play-label"].textContent = playing ? this.dataset.labelPause : this.dataset.labelPlay;
  }
}

if (!customElements.get("rl-live-map")) {
  customElements.define("rl-live-map", LiveMap);
}
