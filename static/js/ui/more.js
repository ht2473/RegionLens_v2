/* «Ещё N» у перечня: кнопка после перечня прячет и показывает лишние пункты.
     <ol id="…" data-more-list> … <li data-more-item> … </ol>
     <button data-more-toggle aria-controls="…" aria-expanded="true" hidden
             data-more-label="Ещё 7" data-less-label="Свернуть">Ещё 7</button> */

/** Показать или спрятать лишние пункты перечня. */
function setExpanded(toggle, list, expanded) {
  list.querySelectorAll("[data-more-item]").forEach((item) => {
    item.hidden = !expanded;
  });
  toggle.setAttribute("aria-expanded", String(expanded));
  toggle.textContent = expanded ? toggle.dataset.lessLabel : toggle.dataset.moreLabel;
}

/** Подключить кнопки «Ещё N» поддерева; каждая подключается один раз. */
export function enhance(root = document) {
  root.querySelectorAll("[data-more-toggle]:not([data-bound])").forEach((toggle) => {
    const list = document.getElementById(toggle.getAttribute("aria-controls"));
    if (!list) {
      return;
    }
    toggle.dataset.bound = "";
    toggle.hidden = false;
    setExpanded(toggle, list, false);
    toggle.addEventListener("click", () => {
      const expanded = toggle.getAttribute("aria-expanded") !== "true";
      setExpanded(toggle, list, expanded);
      // После сворачивания кнопка возвращается в поле зрения.
      if (!expanded && toggle.getBoundingClientRect().top < 0) {
        toggle.scrollIntoView({ block: "nearest" });
      }
    });
  });
}

export function init() {
  enhance(document);
}
