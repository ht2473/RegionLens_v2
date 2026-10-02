/* Быстрый переход: перебор найденного стрелками, переход по Enter; список приходит
   с сервера, поэтому отмеченная строка ищется в разметке заново. */

import * as dialogs from "./dialogs.js";

const PALETTE_ID = "palette";

/* --- Недавно открытое: при пустом поле; хранится в браузере ------------------------
   Запись помнит язык страницы: в перечне — только страницы языка, на котором он открыт. */

const RECENT_KEY = "regionlens:recent";
const RECENT_LIMIT = 6;

const recent = {
  /** Прочитать перечень, пережив испорченную или недоступную запись; записи без языка — отбросить. */
  read() {
    try {
      const stored = JSON.parse(window.localStorage.getItem(RECENT_KEY) || "[]");
      return Array.isArray(stored)
        ? stored.filter((item) => item && item.url && item.title && item.lang)
        : [];
    } catch (error) {
      return [];
    }
  },

  /** Язык текущей страницы. */
  language() {
    return document.documentElement.lang || "ru";
  },

  /** Запомнить открытую страницу. */
  remember() {
    // Главная в перечень не идёт: до неё один щелчок по знаку в шапке.
    const path = window.location.pathname + window.location.search;
    const home = document.querySelector(".app-header__brand");
    if (home && home.getAttribute("href") === window.location.pathname) {
      return;
    }

    const title = document.title.split(" — ")[0];
    if (!title) {
      return;
    }

    const lang = recent.language();
    const stored = recent.read().filter((item) => item.url !== path);
    const same = [{ url: path, title: title, lang: lang }, ...stored.filter((item) => item.lang === lang)];
    const other = stored.filter((item) => item.lang !== lang);
    try {
      window.localStorage.setItem(RECENT_KEY, JSON.stringify([...same.slice(0, RECENT_LIMIT), ...other]));
    } catch (error) {
      // Запись недоступна — перечень недавнего необязателен.
    }
  },

  /** Показать перечень в палитре. */
  show(results, title) {
    const lang = recent.language();
    const items = recent.read().filter((item) => item.lang === lang);
    if (!items.length || !results) {
      return;
    }

    const heading = document.createElement("div");
    heading.className = "palette__group-title";
    heading.textContent = title;

    const list = document.createDocumentFragment();
    list.appendChild(heading);
    items.forEach((item) => {
      const link = document.createElement("a");
      link.className = "palette__item";
      link.href = item.url;
      const label = document.createElement("span");
      label.className = "palette__item-title";
      // Название — текстом: из хранилища браузера оно могло вернуться изменённым.
      label.textContent = item.title;
      link.appendChild(label);
      list.appendChild(link);
    });

    results.replaceChildren(list);
  },
};

/** Перечень найденного. */
function items() {
  const panel = document.getElementById(PALETTE_ID);
  return panel ? [...panel.querySelectorAll(".palette__item")] : [];
}

/** Отметить строку под номером классом is-selected, не выходя за границы списка. */
function mark(index) {
  const found = items();
  if (!found.length) {
    return;
  }
  const chosen = Math.min(Math.max(index, 0), found.length - 1);
  found.forEach((item, at) => item.classList.toggle("is-selected", at === chosen));
  found[chosen].scrollIntoView({ block: "nearest" });
}

/** Номер отмеченной строки или −1. */
function current() {
  return items().findIndex((item) => item.classList.contains("is-selected"));
}

/** Раскрыть палитру, перенеся в её поле знак, набранный на кнопке вызова. */
function openWith(char) {
  dialogs.open(PALETTE_ID);
  const field = document.querySelector(".palette__input");
  if (!field) {
    return;
  }
  field.value = char;
  field.setSelectionRange(char.length, char.length);
  // input вручную: значение изменено из кода.
  field.dispatchEvent(new Event("input", { bubbles: true }));
}

export function init() {
  recent.remember();

  // Ctrl+K или Cmd+K открывает быстрый переход.
  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      dialogs.open(PALETTE_ID);
    }
  });

  const panel = document.getElementById(PALETTE_ID);
  if (!panel) {
    return;
  }

  // Пустая палитра предлагает недавнее при фокусе в поле.
  const field = panel.querySelector(".palette__input");
  const results = panel.querySelector("#palette-results");
  if (field && panel.dataset.recentTitle) {
    field.addEventListener("focus", () => {
      if (!field.value) {
        recent.show(results, panel.dataset.recentTitle);
        mark(0);
      }
    });
  }

  document.querySelectorAll(`[data-dialog-open="${PALETTE_ID}"]`).forEach((trigger) => {
    trigger.addEventListener("keydown", (event) => {
      if (event.ctrlKey || event.metaKey || event.altKey) {
        return;
      }
      // Печатный знак — одна буква; пробел оставлен кнопке.
      if (event.key.length !== 1 || event.key === " ") {
        return;
      }
      event.preventDefault();
      openWith(event.key);
    });
  });

  panel.addEventListener("keydown", (event) => {
    // Esc закрывает палитру с первого нажатия, а не стирает поле.
    if (event.key === "Escape" && panel.open) {
      event.preventDefault();
      panel.close();
      return;
    }
    const found = items();
    if (!found.length) {
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      const at = current();
      const start = step > 0 ? 0 : found.length - 1;
      mark(at === -1 ? start : at + step);
    } else if (event.key === "Enter") {
      const at = current();
      if (at !== -1) {
        event.preventDefault();
        found[at].click();
      }
    }
  });

  // Исправление опечатки подставляется в поле, палитра остаётся раскрытой.
  panel.addEventListener("click", (event) => {
    const suggestion = event.target.closest("[data-palette-suggest]");
    if (suggestion) {
      event.preventDefault();
      openWith(suggestion.dataset.paletteSuggest);
    }
  });

  // Новый список — без прежней отметки.
  panel.addEventListener("htmx:afterSwap", () => mark(0));
}
