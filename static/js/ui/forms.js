/* Надстройки над формами, работающими и без сценариев: счётчики, отбор по мере ввода,
   значение в заголовке свёрнутого поля, отправка при выборе. enhance(root) идемпотентна. */

/* --- Счётчик отмеченного: заголовок рейля не приходит с фрагментами --------------- */

/** Обновить все счётчики страницы. */
export function refreshCounters() {
  document.querySelectorAll("[data-count-of]").forEach((node) => {
    const scope = document.querySelector(node.dataset.countOf);
    if (!scope) {
      return;
    }
    const checked = scope.querySelectorAll("input[type=checkbox]:checked").length;
    const limit = node.dataset.countMax;
    node.textContent = limit ? `(${checked}/${limit})` : String(checked);
  });
}

/* --- Отбор в готовом перечне по мере ввода; без сценариев отбирает сервер ------------ */

// «Ё» приводится к «е».
const normalize = (value) => value.toLowerCase().replace(/ё/g, "е").trim();

function enhanceFilters(root) {
  root.querySelectorAll("[data-filter-input]").forEach((input) => {
    if (input.dataset.filterReady === "true") {
      return;
    }
    if (!document.querySelector(input.dataset.filterInput)) {
      return;
    }
    input.dataset.filterReady = "true";

    // Перечень ищется при каждом вводе: его могут заменить фрагментом (панель исследования).
    function apply() {
      const scope = document.querySelector(input.dataset.filterInput);
      if (!scope) {
        return;
      }
      const items = [...scope.querySelectorAll("[data-filter-item]")];
      const groups = [...scope.querySelectorAll("[data-filter-group]")];
      const empty = input.dataset.filterEmpty
        ? document.querySelector(input.dataset.filterEmpty)
        : null;
      // Ввод раскрывает свёрнутый перечень.
      const holder = scope.closest("details");
      const haystacks = new Map(
        items.map((item) => [item, normalize(item.dataset.filterText || item.textContent)])
      );
      const query = normalize(input.value);
      let shown = 0;

      if (query && holder && !holder.open) {
        holder.open = true;
      }

      items.forEach((item) => {
        const match = !query || haystacks.get(item).includes(query);
        item.hidden = !match;
        if (match) {
          shown += 1;
        }
      });

      groups.forEach((group) => {
        const visible = [...group.querySelectorAll("[data-filter-item]")].filter(
          (item) => !item.hidden
        );
        group.hidden = visible.length === 0;
        // Свёрнутая группа с найденным раскрывается.
        if (query && visible.length && group.tagName === "DETAILS") {
          group.open = true;
        }
        const counter = group.querySelector("[data-filter-count]");
        if (counter) {
          counter.textContent = String(visible.length);
        }
      });

      if (empty) {
        empty.hidden = shown !== 0;
      }
    }

    input.addEventListener("input", apply);
    // Кнопка очистки поля типа search события input не порождает во всех браузерах.
    input.addEventListener("search", apply);
    apply();
  });
}

/* --- Значение в заголовке свёрнутого поля: рейль при пересчёте не заменяется ---------- */

function enhanceSummaries(root) {
  root.querySelectorAll("[data-summary-of]").forEach((output) => {
    if (output.dataset.summaryReady === "true") {
      return;
    }
    const field = document.getElementById(output.dataset.summaryOf);
    if (!field) {
      return;
    }
    output.dataset.summaryReady = "true";
    field.addEventListener("change", () => {
      const option = field.selectedOptions && field.selectedOptions[0];
      if (option) {
        output.textContent = option.textContent.trim();
      }
    });
  });
}

/* --- Отборы, применяемые сразу (data-autosubmit); поиск — по Enter; кнопка скрыта,
   если поля поиска нет. ------------------------------------------------------------- */

function enhanceAutoSubmit(root) {
  root.querySelectorAll("form[data-autosubmit]").forEach((form) => {
    if (form.dataset.autosubmitReady === "true") {
      return;
    }
    form.dataset.autosubmitReady = "true";
    if (!form.querySelector('input[type="search"], input[type="text"]')) {
      form.querySelectorAll("[data-autosubmit-button]").forEach((button) => {
        button.hidden = true;
      });
    }
    form.addEventListener("change", (event) => {
      if (event.target.matches("select, input[type='checkbox'], input[type='radio']")) {
        form.requestSubmit();
      }
    });
  });
}

/* --------------------------------------------------------------------------------- */

/** Обслужить формы в поддереве: при загрузке и в каждом новом фрагменте. */
export function enhance(root = document) {
  enhanceFilters(root);
  enhanceSummaries(root);
  enhanceAutoSubmit(root);
}

export function init() {
  document.addEventListener("change", (event) => {
    if (event.target && event.target.type === "checkbox") {
      refreshCounters();
    }
  });
  refreshCounters();
  enhance(document);
}
