/* Прокручиваемые области: таблица или перечень, не помещающиеся в рамку, получают фокус
   с клавиатуры и имя для программ чтения; когда помещаются — снова обычный блок. */

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

/** Поставить или снять фокус области по тому, прокручивается ли она сейчас. */
function update(area) {
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

/** Следить за областями поддерева; каждая подключается один раз. */
export function enhance(root = document) {
  if (!observer) {
    return;
  }
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
