/* Поле выбора с поиском <rl-combobox> над select, который остаётся источником значения
   и работает без сценариев; определённый элемент прячет его правилом rl-combobox:defined > select.
   Подписки на окно и документ снимаются, когда элемент уносят. */

// Наименьшая ширина раскрытой панели: длинные названия рядов — в две строки.
const MIN_WIDTH = 420;

// Отступ панели от края окна, наибольшая её высота и наименьшая высота,
// при которой она ещё раскрывается вниз, а не вверх.
const EDGE = 12;
const MAX_HEIGHT = 420;
const MIN_HEIGHT = 220;

// Ширина, с которой рейль — колонка; ниже он лист, и панель не выносится (как в layout.css).
const floating = window.matchMedia("(min-width: 768px)");

/** Привести строку к виду, удобному для поиска. */
function normalize(value) {
  return value.toLowerCase().replace(/ё/g, "е").replace(/\s+/g, " ").trim();
}

/** Сокращение по первым буквам значимых слов: «ВРП» находит «валовой региональный продукт». */
function initials(value) {
  return normalize(value)
    .split(/[^0-9a-zа-я]+/)
    .filter((word) => word.length > 2)
    .map((word) => word[0])
    .join("");
}

class Combobox extends HTMLElement {
  connectedCallback() {
    if (!this.select) {
      this.select = this.querySelector("select");
      if (!this.select || this.select.multiple) {
        this.select = null;
        return;
      }
      this.build();
    }
    this.onDocumentClick = (event) => {
      if (!this.panel.hidden && !this.contains(event.target)) {
        this.close({ focus: false });
      }
    };
    document.addEventListener("click", this.onDocumentClick);
    this.scheduleLoad();
  }

  disconnectedCallback() {
    if (!this.select) {
      return;
    }
    document.removeEventListener("click", this.onDocumentClick);
    this.close({ focus: false });
  }

  /** Строки интерфейса: приходят атрибутами, чтобы попасть в каталог перевода. */
  get strings() {
    const data = this.dataset;
    return {
      search: data.search || "Поиск",
      empty: data.empty || "Ничего не найдено",
      // Подпись кнопки, когда выбирать не из чего.
      placeholder: data.placeholder || "Не выбрано",
      // Счётчик найденного; без согласования с числом: множественные формы
      // сборщик переводов не поддерживает.
      found: data.found || "найдено",
      total: data.total || "всего",
      loading: data.loading || "Загрузка…",
      unnormalised: data.unnormalised || "абс.",
      unnormalisedHint: data.unnormalisedHint || "",
    };
  }

  /** Построить кнопку и панель; вызывается один раз за жизнь элемента. */
  build() {
    const select = this.select;
    const strings = this.strings;

    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "combobox__trigger";
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");
    if (select.id) {
      trigger.setAttribute("aria-labelledby", `${select.id}-label ${select.id}-value`);
    }
    trigger.innerHTML =
      '<span class="combobox__value"></span>' +
      '<svg class="combobox__chevron" aria-hidden="true"><use href="#icon-chevron-down"></use></svg>';
    this.appendChild(trigger);

    this.valueLabel = trigger.querySelector(".combobox__value");
    if (select.id) {
      this.valueLabel.id = `${select.id}-value`;
    }

    const panel = document.createElement("div");
    panel.className = "combobox__panel";
    panel.hidden = true;
    panel.innerHTML =
      '<div class="combobox__search"><input type="search" autocomplete="off" spellcheck="false">' +
      '<span class="combobox__count" aria-live="polite"></span></div>' +
      '<ul class="combobox__list" role="listbox" tabindex="-1"></ul>' +
      '<p class="combobox__empty" hidden></p>';
    this.appendChild(panel);

    this.trigger = trigger;
    this.panel = panel;
    this.search = panel.querySelector("input");
    this.list = panel.querySelector(".combobox__list");
    this.empty = panel.querySelector(".combobox__empty");
    this.counter = panel.querySelector(".combobox__count");
    this.search.placeholder = strings.search;
    this.search.setAttribute("aria-label", strings.search);
    this.empty.textContent = strings.empty;

    // В странице только выбранный ряд, перечень догружает load.
    this.loaded = !this.dataset.source;
    this.loading = null;
    this.place = this.place.bind(this);

    this.collect();
    this.bind();
    this.showSelected();
  }

  /** Разложить пункты списка по содержимому select; повторяется после догрузки. */
  collect() {
    const strings = this.strings;
    this.list.textContent = "";
    this.entries = [];
    [...this.select.children].forEach((node) => {
      const group = node.tagName === "OPTGROUP" ? node.label : "";
      const options = node.tagName === "OPTGROUP" ? [...node.children] : [node];

      // Свой контейнер раздела — чтобы закреплённый заголовок уходил вместе с ним.
      let container = this.list;
      if (group) {
        const section = document.createElement("li");
        section.className = "combobox__section";
        section.setAttribute("role", "group");
        section.setAttribute("aria-label", group);

        const heading = document.createElement("div");
        heading.className = "combobox__group";
        // Раздел уже назван меткой контейнера.
        heading.setAttribute("aria-hidden", "true");
        heading.textContent = group;
        // Полное название усечённого заголовка — в подсказке.
        heading.title = group;
        section.appendChild(heading);

        container = document.createElement("ul");
        container.className = "combobox__items";
        container.setAttribute("role", "presentation");
        section.appendChild(container);

        this.list.appendChild(section);
        this.entries.push({ element: section, group: true, haystack: normalize(group) });
      }

      options.forEach((option) => {
        const item = document.createElement("li");
        item.className = "combobox__option";
        item.setAttribute("role", "option");
        item.dataset.value = option.value;
        item.textContent = option.textContent.trim();
        item.title = option.textContent.trim();

        // Пометка ненормированной величины — признак с сервера.
        if (option.dataset.unnormalised === "1") {
          const mark = document.createElement("span");
          mark.className = "series-mark";
          mark.textContent = strings.unnormalised;
          mark.title = strings.unnormalisedHint;
          item.appendChild(mark);
        }

        container.appendChild(item);
        // Слова сборника для поиска ряда основного набора — атрибутом.
        this.entries.push({
          element: item,
          option: option,
          haystack: normalize(`${group} ${option.textContent} ${option.dataset.search || ""}`),
          initials: initials(option.textContent),
        });
      });
    });

    this.options = this.entries.filter((entry) => !entry.group);
  }

  /** Подключить обработчики кнопки, поиска, перечня и клавиатуры. */
  bind() {
    this.trigger.addEventListener("click", () => (this.panel.hidden ? this.open() : this.close()));
    this.search.addEventListener("input", () => this.filter());
    // change поля поиска не выпускается наружу: иначе форма рейля отправила бы лишний запрос.
    this.search.addEventListener("change", (event) => event.stopPropagation());

    this.list.addEventListener("click", (event) => {
      const item = event.target.closest(".combobox__option");
      if (item) {
        this.choose(this.options.find((entry) => entry.element === item));
      }
    });

    this.panel.addEventListener("keydown", (event) => {
      const rest = this.visible();
      const current = rest.findIndex((entry) => entry.element.classList.contains("is-active"));

      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const step = event.key === "ArrowDown" ? 1 : -1;
        this.highlight(rest[Math.min(Math.max(current + step, 0), rest.length - 1)]);
      } else if (event.key === "Enter") {
        event.preventDefault();
        this.choose(rest[current] || rest[0]);
      } else if (event.key === "Escape") {
        event.preventDefault();
        this.close();
      } else if (event.key === "Tab") {
        this.close({ focus: false });
      }
    });

    this.select.addEventListener("change", () => this.showSelected());

    // Нажатие на подпись скрытого списка переводит фокус на кнопку.
    const label = this.select.id
      ? document.querySelector(`label[for="${CSS.escape(this.select.id)}"]`)
      : null;
    if (label) {
      label.addEventListener("click", (event) => {
        event.preventDefault();
        this.trigger.focus();
      });
    }
  }

  /** Догрузить полный перечень, когда браузер свободен, с пределом ожидания. */
  scheduleLoad() {
    if (this.loaded || this.loadScheduled) {
      return;
    }
    this.loadScheduled = true;
    if (window.requestIdleCallback) {
      window.requestIdleCallback(() => this.load(), { timeout: 2000 });
    } else {
      window.setTimeout(() => this.load(), 300);
    }
  }

  /** Забрать полный перечень рядов; ответ по адресу с отпечатком браузер хранит год. */
  load() {
    if (this.loaded) {
      return Promise.resolve();
    }
    if (!this.loading) {
      this.loading = fetch(this.dataset.source, { credentials: "same-origin" })
        .then((response) => (response.ok ? response.text() : Promise.reject(response.status)))
        .then((markup) => {
          const select = this.select;
          const current = select.options[select.selectedIndex] || null;
          select.innerHTML = markup;
          if (current) {
            select.value = current.value;
            // Выбранный ряд вне перечня остаётся в списке, чтобы не подменился первым.
            if (select.value !== current.value) {
              select.prepend(current);
              current.selected = true;
            }
          }
          this.loaded = true;
          this.collect();
          this.showSelected();
          if (!this.panel.hidden) {
            this.filter();
          }
        })
        .catch(() => {
          this.loading = null;
        });
    }
    return this.loading;
  }

  showSelected() {
    const chosen = this.select.options[this.select.selectedIndex];
    this.valueLabel.textContent = chosen ? chosen.textContent.trim() : this.strings.placeholder;
    this.options.forEach((entry) => {
      const active = entry.option === chosen;
      entry.element.classList.toggle("is-selected", active);
      entry.element.setAttribute("aria-selected", active ? "true" : "false");
    });
  }

  highlight(entry) {
    this.options.forEach((item) => item.element.classList.remove("is-active"));
    if (!entry) {
      return;
    }
    entry.element.classList.add("is-active");
    entry.element.scrollIntoView({ block: "nearest" });
  }

  visible() {
    return this.options.filter((entry) => !entry.element.hidden);
  }

  filter() {
    // Каждое слово запроса ищется отдельно: порядок слов не важен.
    const words = normalize(this.search.value).split(" ").filter(Boolean);
    this.options.forEach((entry) => {
      entry.element.hidden =
        words.length > 0 &&
        !words.every((word) => entry.haystack.includes(word) || entry.initials.includes(word));
    });

    // Заголовок раздела скрывается вместе со всеми его пунктами.
    let heading = null;
    let shown = 0;
    this.entries.forEach((entry) => {
      if (entry.group) {
        if (heading) {
          heading.element.hidden = shown === 0;
        }
        heading = entry;
        shown = 0;
        return;
      }
      if (!entry.element.hidden) {
        shown += 1;
      }
    });
    if (heading) {
      heading.element.hidden = shown === 0;
    }

    const rest = this.visible();
    const strings = this.strings;
    this.empty.hidden = rest.length > 0 || !this.loaded;
    this.counter.textContent = this.loaded
      ? `${words.length ? strings.found : strings.total}: ${rest.length}`
      : strings.loading;
    this.highlight(rest[0]);
  }

  /** Поставить раскрытую панель поверх страницы: рейль с прокруткой обрезал бы её. */
  place() {
    const panel = this.panel;
    // В листе с преобразованием position: fixed отсчитывается от листа — панель не выносится.
    if (!floating.matches) {
      panel.classList.remove("is-floating");
      panel.removeAttribute("style");
      return;
    }

    const box = this.trigger.getBoundingClientRect();
    const room = document.documentElement.clientWidth - 2 * EDGE;
    const width = Math.min(Math.max(box.width, MIN_WIDTH), room);
    const below = window.innerHeight - box.bottom - EDGE;
    const above = box.top - EDGE;

    panel.classList.add("is-floating");
    panel.style.width = `${width}px`;
    panel.style.left = `${Math.max(EDGE, Math.min(box.left, room - width + EDGE))}px`;

    // Вниз места нет — панель разворачивается вверх.
    if (below < MIN_HEIGHT && above > below) {
      panel.style.top = "auto";
      panel.style.bottom = `${window.innerHeight - box.top + 4}px`;
      panel.style.maxHeight = `${Math.min(MAX_HEIGHT, above)}px`;
    } else {
      panel.style.bottom = "auto";
      panel.style.top = `${box.bottom + 4}px`;
      panel.style.maxHeight = `${Math.min(MAX_HEIGHT, below)}px`;
    }
  }

  open() {
    // Место — до показа: иначе на кадр видна обрезанная панель внутри рейля.
    this.place();
    this.panel.hidden = false;
    this.trigger.setAttribute("aria-expanded", "true");
    this.search.value = "";
    this.filter();
    const chosen = this.options.find((entry) => entry.option.selected);
    this.highlight(chosen || this.visible()[0]);
    this.search.focus();
    this.load();
    // Прокрутка любого предка (перехват на погружении) и размер окна двигают панель.
    window.addEventListener("scroll", this.place, true);
    window.addEventListener("resize", this.place);
  }

  close({ focus = true } = {}) {
    this.panel.hidden = true;
    this.trigger.setAttribute("aria-expanded", "false");
    window.removeEventListener("scroll", this.place, true);
    window.removeEventListener("resize", this.place);
    if (focus) {
      this.trigger.focus();
    }
  }

  choose(entry) {
    if (!entry) {
      return;
    }
    this.select.value = entry.option.value;
    this.showSelected();
    this.close();
    // change вручную: изменение значения из сценария его не порождает.
    this.select.dispatchEvent(new Event("change", { bubbles: true }));
  }
}

if (!customElements.get("rl-combobox")) {
  customElements.define("rl-combobox", Combobox);
}
