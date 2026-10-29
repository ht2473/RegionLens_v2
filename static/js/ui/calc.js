/* Панель расчёта: подсказка названий показателей в своей формуле. После «[» под полем
   появляются подходящие названия; стрелки выбирают, Enter или щелчок вставляет «[название]»,
   Escape закрывает перечень. Раскрытый перечень показателей прокручивается к выбранному.
   Панель приходит фрагментом — обработчики висят на документе.
   Без сценариев названия пишут сами, предпросмотр — кнопкой «Проверить». */

// Подсказок в перечне — не больше.
const LIMIT = 8;

/** Начатая ссылка перед курсором: место «[» и набранный текст; вне скобок — null. */
function typed(field) {
  const before = field.value.slice(0, field.selectionStart ?? field.value.length);
  const open = before.lastIndexOf("[");
  if (open < 0 || before.lastIndexOf("]") > open) {
    return null;
  }
  return { start: open, text: before.slice(open + 1).toLocaleLowerCase("ru") };
}

function listOf(field) {
  return field.form?.querySelector("[data-calc-suggest]") || null;
}

function namesOf(field) {
  return [...(field.form?.querySelectorAll("[data-calc-names] [data-title]") || [])].map(
    (item) => item.dataset.title
  );
}

function close(field) {
  const list = listOf(field);
  if (list) {
    list.hidden = true;
    list.replaceChildren();
  }
}

/** Показать подходящие названия; совпадение с началом слова — выше. */
function suggest(field) {
  const list = listOf(field);
  const found = typed(field);
  if (!list || !found) {
    close(field);
    return;
  }
  const names = [...new Set(namesOf(field))]
    .map((name) => ({ name, at: name.toLocaleLowerCase("ru").indexOf(found.text) }))
    .filter((item) => item.at >= 0)
    .sort((one, two) => (one.at === 0) - (two.at === 0) || one.name.length - two.name.length)
    .slice(0, LIMIT);
  if (!names.length) {
    close(field);
    return;
  }
  list.replaceChildren(
    ...names.map(({ name }, index) => {
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = name;
      button.dataset.name = name;
      button.classList.toggle("is-active", index === 0);
      item.append(button);
      return item;
    })
  );
  list.hidden = false;
}

/** Вставить «[название]» на место начатой ссылки. */
function choose(field, name) {
  const found = typed(field);
  if (!found) {
    return;
  }
  const end = field.selectionStart ?? field.value.length;
  const after = field.value.slice(end);
  const closing = after.startsWith("]") ? "" : "]";
  field.setRangeText(`[${name}${closing}`, found.start, end, "end");
  if (!closing) {
    field.selectionStart = field.selectionEnd = field.selectionStart + 1;
  }
  close(field);
  field.focus();
}

function move(field, step) {
  const buttons = [...(listOf(field)?.querySelectorAll("button") || [])];
  if (!buttons.length) {
    return;
  }
  const current = buttons.findIndex((button) => button.classList.contains("is-active"));
  const next = (current + step + buttons.length) % buttons.length;
  buttons.forEach((button, index) => button.classList.toggle("is-active", index === next));
  buttons[next].scrollIntoView({ block: "nearest" });
}

export function init() {
  // Раскрытый перечень показателей — сразу у выбранного: он может стоять ниже видимой части.
  // Событие toggle не всплывает — ловится на погружении.
  document.addEventListener(
    "toggle",
    (event) => {
      const pick = event.target;
      if (pick.matches?.(".series-pick") && pick.open) {
        const chosen = pick.querySelector("input[type=radio]:checked")?.closest("label");
        // Таблица выбранного раскрыта, даже если её свернули прежде.
        chosen?.closest(".series-pick__group")?.setAttribute("open", "");
        chosen?.scrollIntoView({ block: "nearest" });
      }
    },
    true
  );
  document.addEventListener("input", (event) => {
    if (event.target.matches?.("[data-calc-expression]")) {
      suggest(event.target);
    }
  });
  document.addEventListener("keydown", (event) => {
    const field = event.target;
    if (!field.matches?.("[data-calc-expression]") || listOf(field)?.hidden !== false) {
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      move(field, event.key === "ArrowDown" ? 1 : -1);
    } else if (event.key === "Enter") {
      const active = listOf(field).querySelector("button.is-active");
      if (active) {
        event.preventDefault();
        choose(field, active.dataset.name);
      }
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      close(field);
    }
  });
  // Нажатие по подсказке не уводит фокус из поля: уход фокуса пересчитал бы панель
  // по ещё не вставленному названию.
  document.addEventListener("mousedown", (event) => {
    if (event.target.closest?.("[data-calc-suggest] button")) {
      event.preventDefault();
    }
  });
  document.addEventListener("click", (event) => {
    const button = event.target.closest?.("[data-calc-suggest] button");
    if (button) {
      const field = button.closest("form")?.querySelector("[data-calc-expression]");
      if (field) {
        choose(field, button.dataset.name);
      }
    }
  });
}
