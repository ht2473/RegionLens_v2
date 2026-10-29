/* Место всплывающих панелей (popover) у их кнопки; без сценариев панель посреди окна,
   на телефоне — лист. anchor positioning не берём: в Firefox он слишком новый.
   Панель держится за окно (position: fixed) и при прокрутке страницы, рейля или окна
   переставляется за кнопкой: кнопка в закреплённом рейле при прокрутке документа стоит
   на месте, и панель, привязанная к документу, уезжала бы от неё. Под кнопкой места
   нет, а над ней есть — панель встаёт над кнопкой. */

const SHEET_MENU_QUERY = "(max-width: 560px)";
// Зазор между кнопкой и панелью и отступ панели от края окна.
const GAP = 6;
const EDGE = 8;

function invokerOf(panel) {
  return panel.id ? document.querySelector(`[popovertarget="${CSS.escape(panel.id)}"]`) : null;
}

function reset(panel) {
  delete panel.dataset.anchored;
  panel.style.removeProperty("top");
  panel.style.removeProperty("left");
  panel.style.removeProperty("translate");
}

/** Поставить панель у кнопки в координатах окна; высота известна, когда панель открыта. */
function position(panel, invoker) {
  const box = invoker.getBoundingClientRect();
  const height = panel.matches(":popover-open") ? panel.offsetHeight : 0;
  const below = box.bottom + GAP;
  const above = box.top - GAP - height;
  const fitsBelow = below + height <= window.innerHeight - EDGE;
  const top = height && !fitsBelow && above >= EDGE ? above : below;
  panel.style.top = `${Math.max(top, EDGE)}px`;
  // Выравнивание по краю кнопки со стороны середины окна; правый край — сдвигом
  // на свою ширину: right промахивается на ширину полосы прокрутки.
  const alignRight = box.left + box.width / 2 > document.documentElement.clientWidth / 2;
  panel.style.left = `${alignRight ? box.right : box.left}px`;
  panel.style.translate = alignRight ? "-100% 0" : "none";
}

function place(panel) {
  const invoker = invokerOf(panel);
  if (!invoker || window.matchMedia(SHEET_MENU_QUERY).matches) {
    reset(panel);
    return;
  }
  panel.dataset.anchored = "";
  position(panel, invoker);
}

/** Открытые привязанные панели — за своими кнопками; кнопка ушла из окна — панель закрыта. */
function follow() {
  document.querySelectorAll(".menu-popover[data-anchored]:popover-open").forEach((panel) => {
    const invoker = invokerOf(panel);
    const box = invoker?.getBoundingClientRect();
    if (!box || box.bottom < 0 || box.top > window.innerHeight || box.width === 0) {
      panel.hidePopover();
      return;
    }
    position(panel, invoker);
  });
}

export function init() {
  // beforetoggle и toggle не всплывают — перехват на погружении покрывает и панели из HTMX.
  document.addEventListener(
    "beforetoggle",
    (event) => {
      const panel = event.target;
      if (event.newState === "open" && panel.classList?.contains("menu-popover")) {
        place(panel);
      }
    },
    true
  );
  // Открытая панель уже имеет высоту (и содержимое, которое сценарии вставляют при
  // открытии): второй расчёт ставит её над кнопкой, если под ней не помещается.
  document.addEventListener(
    "toggle",
    (event) => {
      const panel = event.target;
      if (event.newState === "open" && panel.dataset?.anchored !== undefined) {
        requestAnimationFrame(() => place(panel));
      }
    },
    true
  );
  // Прокрутка документа, рейля или окна (scroll не всплывает — тоже на погружении).
  let pending = false;
  document.addEventListener(
    "scroll",
    () => {
      if (pending) {
        return;
      }
      pending = true;
      requestAnimationFrame(() => {
        pending = false;
        follow();
      });
    },
    { capture: true, passive: true }
  );
  // При смене ширины окна привязанная панель закрывается; лист — нет: окно меняет
  // высоту, когда выезжает клавиатура.
  window.addEventListener("resize", () => {
    document
      .querySelectorAll(".menu-popover[data-anchored]:popover-open")
      .forEach((panel) => panel.hidePopover());
  });
}
