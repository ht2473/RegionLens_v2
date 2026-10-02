/* Окна и панели шапки средствами браузера. Панели — popover с popovertarget (место ставит
   popovers.js); быстрый переход и выдвижное меню — <dialog>: открывает кнопка
   data-dialog-open="<id>", закрывает data-dialog-close, data-dialog-light — ещё и нажатие мимо. */

/** Открыть окно по опознавателю; прочие открытые окна закрываются. */
export function open(id) {
  const dialog = document.getElementById(id);
  if (!dialog || typeof dialog.showModal !== "function") {
    return null;
  }
  document.querySelectorAll("dialog[open]").forEach((other) => {
    if (other !== dialog) {
      other.close();
    }
  });
  if (!dialog.open) {
    dialog.showModal();
  }
  return dialog;
}

/* Разделы шапки — как строка меню: при раскрытой панели наведение переключает раздел;
   уход фокуса клавишей Tab закрывает панель. */
function initMenuBar() {
  const frames = [...document.querySelectorAll("[data-menu-switch]")];
  const panelOf = (frame) => frame.querySelector("[popover]");
  const browsing = () => frames.some((frame) => panelOf(frame)?.matches(":popover-open"));

  frames.forEach((frame) => {
    const panel = panelOf(frame);
    if (!panel || typeof panel.showPopover !== "function") {
      return;
    }
    frame.addEventListener("mouseenter", () => {
      if (!panel.matches(":popover-open") && browsing()) {
        panel.showPopover();
      }
    });
    frame.addEventListener("focusout", (event) => {
      if (
        event.relatedTarget &&
        !frame.contains(event.relatedTarget) &&
        panel.matches(":popover-open")
      ) {
        panel.hidePopover();
      }
    });
  });
}

export function init() {
  // «Мимо» — только если и начато по самому окну, а не выделением в поле.
  let pressedOn = null;
  document.addEventListener("pointerdown", (event) => {
    pressedOn = event.target;
  });

  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!target.closest) {
      return;
    }
    const opener = target.closest("[data-dialog-open]");
    if (opener) {
      event.preventDefault();
      open(opener.dataset.dialogOpen);
      return;
    }
    const closer = target.closest("[data-dialog-close]");
    if (closer) {
      closer.closest("dialog")?.close();
      return;
    }
    if (
      target instanceof HTMLDialogElement &&
      target.open &&
      target.hasAttribute("data-dialog-light") &&
      pressedOn === target
    ) {
      target.close();
    }
  });

  initMenuBar();
}
