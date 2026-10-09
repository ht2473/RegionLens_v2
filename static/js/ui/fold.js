/* Свёрнутое: блок раскрыт в разметке, сценарий сворачивает его при загрузке.
     data-fold-narrow — только на телефоне; data-fold — на любой ширине.
     <details class="fold" data-fold-narrow open> — переключатель summary;
     <tbody data-fold> — переключатель [data-fold-toggle hidden] в строке заголовка.
   [data-fold-all aria-controls="…" hidden] раскрывает или сворачивает все data-fold блока.
   <tbody data-fold data-fold-src="?rows=…"> — строки приходят при первом раскрытии: ответ —
   <table> с <tbody data-rows="id группы">; у таблицы data-fold-src — все группы одним запросом. */

// Граница — как в components.css (.fold > summary).
const NARROW = window.matchMedia("(max-width: 639px)");

const FOLDABLE = "[data-fold], [data-fold-narrow]";

/** Свёрнут ли блок любого вида. */
function isFolded(block) {
  return block instanceof HTMLDetailsElement ? !block.open : block.classList.contains("is-folded");
}

/** Подписать кнопки «Развернуть все / Свернуть все» по состоянию их блоков. */
function syncAll() {
  document.querySelectorAll("[data-fold-all]").forEach((button) => {
    const scope = document.getElementById(button.getAttribute("aria-controls"));
    if (!scope) {
      return;
    }
    const folded = [...scope.querySelectorAll("[data-fold]")].some(isFolded);
    button.textContent = folded ? button.dataset.openLabel : button.dataset.closeLabel;
    button.setAttribute("aria-expanded", String(!folded));
  });
}

/** Группы, строки которых ещё не пришли. */
function unloaded(blocks) {
  return blocks.filter((block) => block.dataset.foldSrc && !("loaded" in block.dataset));
}

/**
 * Догрузить строки групп: одной — её адресом, нескольких — адресом таблицы.
 * Без ответа — та же страница со всей таблицей.
 */
function loadRows(blocks) {
  const pending = unloaded(blocks);
  if (!pending.length) {
    return;
  }
  const table = pending[0].closest("[data-fold-src]:not(tbody)");
  const source = pending.length > 1 && table ? table.dataset.foldSrc : pending[0].dataset.foldSrc;
  pending.forEach((block) => {
    block.dataset.loaded = "";
    block.setAttribute("aria-busy", "true");
  });
  fetch(new URL(source, window.location.href), { credentials: "same-origin" })
    .then((response) => (response.ok ? response.text() : Promise.reject(response.status)))
    .then((markup) => {
      const template = document.createElement("template");
      template.innerHTML = markup;
      template.content.querySelectorAll("tbody[data-rows]").forEach((group) => {
        const target = document.getElementById(group.dataset.rows);
        if (target && pending.includes(target)) {
          target.append(...group.children);
        }
      });
    })
    .catch(() => {
      const url = new URL(window.location.href);
      url.searchParams.set("themes", "all");
      url.hash = pending[0].id;
      window.location.assign(url);
    })
    .finally(() => pending.forEach((block) => block.removeAttribute("aria-busy")));
}

/** Раскрыть или свернуть блок любого вида. */
function setOpen(block, open) {
  if (open) {
    loadRows([block]);
  }
  if (block instanceof HTMLDetailsElement) {
    block.open = open;
  } else {
    block.classList.toggle("is-folded", !open);
    block.querySelector("[data-fold-toggle]")?.setAttribute("aria-expanded", String(open));
  }
  syncAll();
}

/** Свернуть группу строк и открыть её переключатель. */
function foldGroup(group) {
  const toggle = group.querySelector("[data-fold-toggle]");
  if (!toggle) {
    return;
  }
  toggle.hidden = false;
  if (!toggle.dataset.bound) {
    toggle.dataset.bound = "";
    toggle.addEventListener("click", () => setOpen(group, group.classList.contains("is-folded")));
  }
  setOpen(group, false);
}

/** Элемент, на который указывает адрес после «#», или null. */
function hashTarget() {
  if (!window.location.hash) {
    return null;
  }
  return document.getElementById(decodeURIComponent(window.location.hash.slice(1)));
}

/** Раскрыть свёрнутое вокруг цели ссылки. */
function openAround(target) {
  if (!target) {
    return;
  }
  const around = target.closest(FOLDABLE);
  if (around) {
    setOpen(around, true);
  }
  target.querySelectorAll(FOLDABLE).forEach((block) => setOpen(block, true));
}

/** Подключить кнопки «Развернуть все» поддерева. */
function bindAll(root) {
  root.querySelectorAll("[data-fold-all]:not([data-bound])").forEach((button) => {
    const scope = document.getElementById(button.getAttribute("aria-controls"));
    if (!scope) {
      return;
    }
    button.dataset.bound = "";
    button.hidden = false;
    button.addEventListener("click", () => {
      const blocks = [...scope.querySelectorAll("[data-fold]")];
      const open = blocks.some(isFolded);
      if (open) {
        loadRows(blocks);
      }
      blocks.forEach((block) => setOpen(block, open));
    });
  });
}

/**
 * Свернуть блоки поддерева: data-fold — всегда, data-fold-narrow — на узком экране.
 *
 * Каждый блок сворачивается один раз: раскрытое читателем повторно не закрывается.
 */
export function enhance(root = document) {
  const selector = NARROW.matches
    ? "[data-fold]:not([data-folded]), [data-fold-narrow]:not([data-folded])"
    : "[data-fold]:not([data-folded])";
  root.querySelectorAll(selector).forEach((block) => {
    block.dataset.folded = "";
    if (block instanceof HTMLDetailsElement) {
      block.open = false;
    } else {
      foldGroup(block);
    }
  });
  bindAll(root);
  openAround(hashTarget());
  syncAll();
}

export function init() {
  enhance(document);
  window.addEventListener("hashchange", () => openAround(hashTarget()));
  // Ссылка на уже открытый в адресе раздел hashchange не даёт — цель раскрывается здесь.
  document.addEventListener("click", (event) => {
    const link = event.target.closest?.('a[href^="#"]');
    if (link && link.hash === window.location.hash) {
      openAround(hashTarget());
    }
  });
  // При расширении окна блоки data-fold-narrow раскрываются: переключателей там нет.
  NARROW.addEventListener("change", (event) => {
    if (event.matches) {
      return;
    }
    document.querySelectorAll("[data-fold-narrow]").forEach((block) => {
      setOpen(block, true);
      block.querySelector("[data-fold-toggle]")?.setAttribute("hidden", "");
      delete block.dataset.folded;
    });
  });
}
