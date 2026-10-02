/* Отметка текущего раздела в оглавлении: заголовок, последним дошедший до линии чтения —
   нижнего края шапки, строки пути и полосы-пилюль. Прокручивается только само оглавление. */

import { stickyOffset } from "../lib/tokens.js";

// Зазор между полосой-пилюль и заголовком раздела после перехода по ссылке.
const GAP = 24;

// Доля высоты под линией чтения, в которой раздел уже считается читаемым: при большей
// доле короткий раздел методики уступал бы отметку следующему сразу после перехода к нему.
const READING_SHARE = 0.15;

function watch(toc) {
  const links = [...toc.querySelectorAll('a[href^="#"]')]
    .map((link) => ({ link, target: document.getElementById(decodeURIComponent(link.hash.slice(1))) }))
    .filter((entry) => entry.target);
  if (!links.length) {
    return;
  }

  const bar = toc.classList.contains("toc-pills");
  const strip = bar ? toc.querySelector("ul") || toc : toc;
  if (bar) {
    addArrows(toc, strip);
  }
  let current = null;
  // Во время плавной прокрутки после нажатия отметка держится.
  let heldUntil = 0;

  /** Нижний край закреплённой части экрана. */
  function readingLine() {
    return bar ? toc.getBoundingClientRect().bottom : stickyOffset();
  }

  /** Отступ заголовка после перехода — по настоящей высоте полосы-пилюль с зазором. */
  function syncOffset() {
    if (bar) {
      const height = toc.getBoundingClientRect().height;
      const root = document.documentElement.style;
      root.setProperty("--toc-offset", `${stickyOffset() + height + GAP}px`);
      // Нижний край полосы — для того, что закрепляется под ней (шапка таблицы паспорта).
      root.setProperty("--toc-bottom", `${stickyOffset() + height}px`);
    }
  }

  /** Показать отмеченную ссылку, прокрутив только само оглавление. */
  function reveal(link) {
    const box = link.getBoundingClientRect();
    const frame = strip.getBoundingClientRect();
    if (bar) {
      if (box.left < frame.left || box.right > frame.right) {
        strip.scrollBy({ left: box.left - frame.left - (frame.width - box.width) / 2, behavior: "smooth" });
      }
    } else if (box.top < frame.top || box.bottom > frame.bottom) {
      strip.scrollBy({ top: box.top - frame.top - (frame.height - box.height) / 2, behavior: "smooth" });
    }
  }

  function mark(entry) {
    if (entry === current) {
      return;
    }
    if (current) {
      current.link.classList.remove("is-current");
    }
    current = entry;
    if (current) {
      current.link.classList.add("is-current");
      reveal(current.link);
    }
  }

  function update() {
    if (performance.now() < heldUntil) {
      return;
    }
    const line = readingLine();
    const reach = line + (window.innerHeight - line) * READING_SHARE;
    let found = null;
    links.forEach((entry) => {
      if (entry.target.getBoundingClientRect().top <= reach) {
        found = entry;
      }
    });
    // Дочитанная до конца страница отмечает последний раздел.
    const root = document.documentElement;
    if (window.scrollY + window.innerHeight >= root.scrollHeight - 2) {
      found = links[links.length - 1];
    }
    mark(found);
  }

  let scheduled = false;
  function schedule() {
    if (!scheduled) {
      scheduled = true;
      window.requestAnimationFrame(() => {
        scheduled = false;
        update();
      });
    }
  }

  // Нажатая ссылка отмечается сразу, не дожидаясь конца плавной прокрутки.
  toc.addEventListener("click", (event) => {
    const link = event.target.closest('a[href^="#"]');
    const entry = link && links.find((item) => item.link === link);
    if (entry) {
      heldUntil = performance.now() + 1500;
      mark(entry);
    }
  });
  // По окончании прокрутки отмеченным остаётся нажатое.
  window.addEventListener("scrollend", () => {
    if (heldUntil) {
      heldUntil = 0;
      return;
    }
    update();
  });

  window.addEventListener("scroll", schedule, { passive: true });
  window.addEventListener("resize", () => {
    syncOffset();
    schedule();
  });
  syncOffset();
  update();
}

/** Стрелки у краёв полосы-пилюль: видна та, с чьей стороны есть скрытые пилюли. */
function addArrows(toc, strip) {
  const labels = document.body.dataset;
  const make = (side, label, direction) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `toc-pills__arrow toc-pills__arrow--${side}`;
    button.title = label || "";
    // Пилюли доступны клавишей Tab, стрелки нужны только указателю.
    button.tabIndex = -1;
    button.setAttribute("aria-hidden", "true");
    button.innerHTML = '<svg aria-hidden="true"><use href="#icon-chevron-down"></use></svg>';
    button.hidden = true;
    button.addEventListener("click", () => {
      strip.scrollBy({ left: direction * strip.clientWidth * 0.7, behavior: "smooth" });
    });
    toc.appendChild(button);
    return button;
  };
  const back = make("back", labels.scrollBack, -1);
  const forward = make("forward", labels.scrollForward, 1);

  function sync() {
    const hidden = strip.scrollWidth - strip.clientWidth;
    back.hidden = strip.scrollLeft <= 1;
    forward.hidden = strip.scrollLeft >= hidden - 1;
  }

  strip.addEventListener("scroll", sync, { passive: true });
  new ResizeObserver(sync).observe(strip);
  sync();
}

export function init() {
  document.querySelectorAll("[data-toc]").forEach(watch);
}
