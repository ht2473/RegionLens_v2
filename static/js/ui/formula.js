/* Поле формулы своей таблицы: показатель, выбранный в перечне, вставляется в формулу
   там, где стоит курсор, в квадратных скобках. Без сценариев формулу пишут сами. */

/** Вставить текст на место выделения и оставить курсор после него. */
function insert(field, text) {
  const start = field.selectionStart ?? field.value.length;
  const end = field.selectionEnd ?? start;
  field.setRangeText(text, start, end, "end");
  field.dispatchEvent(new Event("input", { bubbles: true }));
}

export function init() {
  document.addEventListener("change", (event) => {
    const select = event.target;
    if (!(select instanceof HTMLSelectElement) || !select.matches("[data-formula-insert]")) {
      return;
    }
    const option = select.selectedOptions[0];
    const field = document.getElementById(select.dataset.formulaInsert);
    if (!option || !option.value || !field) {
      return;
    }
    const label = (option.dataset.label || option.textContent || "").trim();
    insert(field, `[${label}]`);
    // Перечень возвращается к подсказке: следующий выбор — снова вставка.
    select.value = "";
    select.dispatchEvent(new Event("change", { bubbles: true }));
    field.focus();
  });
}
