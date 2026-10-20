/* Прокручиваемые области: таблица или перечень, не помещающиеся в рамку, получают фокус
   с клавиатуры и имя для программ чтения; когда помещаются — снова обычный блок.

   Шапка таблицы данных видна при прокрутке. Таблица помещается по ширине — обёртка
   не прокручивается (data-fits), и шапка закрепляется у страницы под шапкой сайта
   и строкой пути. Широкая и длинная — в рамке высотой в экран (data-tall), шапка
   закреплена в самой рамке. Высоту шапки сайта и строки пути замеряем: на телефоне
   строка пути бывает в две строки. */

const SELECTOR = ".data-table-wrapper, .admin-table-wrapper, .edition-list, .admin-log, .code-block";

/** Имя области: подпись таблицы или заголовок её блока. */
function nameOf(area) {
  const caption = area.querySelector("caption");
  if (caption && caption.textContent.trim()) {
    return caption.textContent.trim();
  }
  const block = area.closest("section, .card, .filter-rail__group");
  const heading = block && block.querySelector("h2, h3, h4, .filter-rail__title");
  return heading ? heading.textContent.trim() : "";
}

/** Отметить, помещается ли таблица данных по ширине и выше ли она экрана. */
function fit(area) {
  const table = area.classList.contains("data-table-wrapper")
    ? area.querySelector(":scope > table")
    : null;
  if (!table) {
    return;
  }
  const fits = table.offsetWidth <= area.clientWidth + 1;
  area.toggleAttribute("data-fits", fits);
  area.toggleAttribute("data-tall", !fits && table.offsetHeight > window.innerHeight * 0.8);
}

/** Поставить или снять фокус области по тому, прокручивается ли она сейчас. */
function update(area) {
  fit(area);
  const scrolls =
    area.scrollWidth > area.clientWidth + 1 || area.scrollHeight > area.clientHeight + 1;
  const marked = area.dataset.scrollFocus !== undefined;
  if (scrolls && !marked) {
    area.dataset.scrollFocus = "";
    area.tabIndex = 0;
    area.setAttribute("role", "region");
    const name = nameOf(area);
    if (name) {
      area.setAttribute("aria-label", name);
    }
  } else if (!scrolls && marked) {
    delete area.dataset.scrollFocus;
    area.removeAttribute("tabindex");
    area.removeAttribute("role");
    area.removeAttribute("aria-label");
  }
}

const observer =
  "ResizeObserver" in window
    ? new ResizeObserver((entries) => entries.forEach((entry) => update(entry.target)))
    : null;

/** Отступ закреплённой шапки таблицы: низ шапки сайта или строки пути. */
function measureTop() {
  const bars = [document.querySelector(".app-header"), document.getElementById("breadcrumbs")];
  const top = bars.reduce((sum, bar) => sum + (bar ? bar.offsetHeight : 0), 0);
  document.documentElement.style.setProperty("--table-head-top", `${top}px`);
}

const bars = "ResizeObserver" in window ? new ResizeObserver(measureTop) : null;

/** Следить за шапкой сайта и строкой пути: строку пути заменяет внеполосный фрагмент. */
function watchBars() {
  if (!bars) {
    return;
  }
  bars.disconnect();
  [document.querySelector(".app-header"), document.getElementById("breadcrumbs")].forEach(
    (bar) => bar && bars.observe(bar),
  );
}

/** Следить за областями поддерева; каждая подключается один раз. */
export function enhance(root = document) {
  if (!observer) {
    return;
  }
  watchBars();
  root.querySelectorAll(SELECTOR).forEach((area) => {
    if (area.dataset.scrollWatch === undefined) {
      area.dataset.scrollWatch = "";
      observer.observe(area);
    }
  });
}

export function init() {
  enhance(document);
}
