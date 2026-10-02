/* Место всплывающих панелей (popover) под их кнопкой — до показа (beforetoggle);
   без сценариев панель посреди окна, на телефоне — лист. anchor positioning не берём:
   в Firefox он слишком новый. */

const SHEET_MENU_QUERY = "(max-width: 560px)";

function place(panel) {
  const invoker = panel.id
    ? document.querySelector(`[popovertarget="${CSS.escape(panel.id)}"]`)
    : null;
  if (!invoker || window.matchMedia(SHEET_MENU_QUERY).matches) {
    delete panel.dataset.anchored;
    panel.style.removeProperty("top");
    panel.style.removeProperty("left");
    panel.style.removeProperty("translate");
    return;
  }
  // Положение — от начала документа: панель прокручивается вместе с кнопкой.
  const box = invoker.getBoundingClientRect();
  panel.dataset.anchored = "";
  // Панели шапки (data-anchor-fixed) держатся за окно: шапка закреплена.
  const fixed = panel.hasAttribute("data-anchor-fixed");
  const scrollY = fixed ? 0 : window.scrollY;
  const scrollX = fixed ? 0 : window.scrollX;
  panel.style.top = `${box.bottom + scrollY + 6}px`;
  // Выравнивание по краю кнопки со стороны середины окна; правый край — сдвигом
  // на свою ширину: right промахивается на ширину полосы прокрутки.
  const alignRight = box.left + box.width / 2 > document.documentElement.clientWidth / 2;
  panel.style.left = `${(alignRight ? box.right : box.left) + scrollX}px`;
  panel.style.translate = alignRight ? "-100% 0" : "none";
}

export function init() {
  // beforetoggle не всплывает — перехват на погружении покрывает и панели из HTMX.
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
  // При смене ширины окна привязанная панель закрывается; лист — нет: окно меняет
  // высоту, когда выезжает клавиатура.
  window.addEventListener("resize", () => {
    document
      .querySelectorAll(".menu-popover[data-anchored]:popover-open")
      .forEach((panel) => panel.hidePopover());
  });
}
