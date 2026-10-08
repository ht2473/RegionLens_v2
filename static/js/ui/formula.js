/* Формула своей таблицы. Показатель из перечня (нажатием, перетаскиванием или выбором
   ряда сайта) встаёт в шаблоне — в выбранный ряд A или B (иначе в первый пустой), в своей
   формуле — туда, где стоит курсор, в квадратных скобках. Брошенный в поле формулы
   показатель встаёт в место броска: поле принимает текст само. Без сценариев A и B
   выбираются из перечня, формулу пишут сами. */

// Тип перетаскиваемых данных: ключ и подпись показателя.
const DRAG_TYPE = "application/x-regionlens-series";

const form = () => document.getElementById("formula-form");

/** Своя формула выбрана — показатели идут в поле формулы. */
function isFree() {
  return Boolean(form()?.querySelector("input[name='template'][value='free']:checked"));
}

/** Вставить текст на место выделения и оставить курсор после него. */
function insert(field, text) {
  const start = field.selectionStart ?? field.value.length;
  const end = field.selectionEnd ?? start;
  field.setRangeText(text, start, end, "end");
  field.dispatchEvent(new Event("input", { bubbles: true }));
}

/** Ряд A или B, куда встанет показатель: отмеченный, иначе первый пустой, иначе A. */
let activeSlot = "a";

function targetSlot() {
  const slots = [...(form()?.querySelectorAll("[data-slot]") || [])];
  const chosen = slots.find((slot) => slot.dataset.slot === activeSlot);
  if (chosen && !chosen.querySelector("select").value) {
    return chosen;
  }
  return slots.find((slot) => !slot.querySelector("select").value) || chosen || slots[0];
}

function markActive(slot) {
  activeSlot = slot.dataset.slot;
  form()?.querySelectorAll("[data-slot]").forEach((item) => {
    item.classList.toggle("is-active", item === slot);
  });
}

/** Поставить показатель в ряд: в перечне ряда его может не быть (ряд сайта) — добавить. */
function fill(slot, key, label) {
  const select = slot.querySelector("select");
  let option = [...select.options].find((item) => item.value === key);
  if (!option) {
    option = new Option(label, key);
    option.dataset.label = label;
    select.add(option, 1);
  }
  select.value = key;
  select.dispatchEvent(new Event("change", { bubbles: true }));
  // Следующий показатель — в другой ряд.
  const other = [...form().querySelectorAll("[data-slot]")].find((item) => item !== slot);
  if (other) {
    markActive(other);
  }
}

/** Показатель в формулу: в шаблоне — в ряд, в своей формуле — в поле у курсора. */
function place(key, label) {
  const root = form();
  if (!root || !key) {
    return;
  }
  if (isFree()) {
    const field = document.getElementById("formula-expression");
    insert(field, `[${label}]`);
    field.focus();
    return;
  }
  const slot = targetSlot();
  if (slot) {
    fill(slot, key, label);
  }
}

/* --- Нажатие и выбор ряда сайта ----------------------------------------------------------- */

function onChipClick(event) {
  const chip = event.target.closest("[data-formula-chip]");
  if (chip) {
    place(chip.dataset.key, chip.dataset.label || chip.textContent.trim());
  }
}

function onSiteChange(event) {
  const select = event.target;
  if (!(select instanceof HTMLSelectElement) || !select.matches("[data-formula-insert]")) {
    return;
  }
  const option = select.selectedOptions[0];
  if (!option || !option.value) {
    return;
  }
  place(option.value, (option.dataset.label || option.textContent || "").trim());
  // Перечень возвращается к подсказке: следующий выбор — снова вставка.
  select.value = "";
  select.dispatchEvent(new Event("change", { bubbles: true }));
}

/* --- Перетаскивание ----------------------------------------------------------------------- */

function onDragStart(event) {
  const chip = event.target.closest?.("[data-formula-chip]");
  if (!chip || !event.dataTransfer) {
    return;
  }
  const label = chip.dataset.label || chip.textContent.trim();
  event.dataTransfer.effectAllowed = "copy";
  event.dataTransfer.setData(DRAG_TYPE, JSON.stringify({ key: chip.dataset.key, label }));
  // Поле формулы принимает текст само — в место броска.
  event.dataTransfer.setData("text/plain", `[${label}]`);
}

function dropTarget(event) {
  return event.target.closest?.("[data-slot], .formula-free");
}

function onDragOver(event) {
  const target = dropTarget(event);
  if (!target || !event.dataTransfer?.types.includes(DRAG_TYPE)) {
    return;
  }
  if (target.matches("[data-slot]")) {
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  }
  target.classList.add("is-over");
}

function onDragLeave(event) {
  const target = dropTarget(event);
  if (target && !target.contains(event.relatedTarget)) {
    target.classList.remove("is-over");
  }
}

function onDrop(event) {
  const target = dropTarget(event);
  form()?.querySelectorAll(".is-over").forEach((item) => item.classList.remove("is-over"));
  if (!target || !target.matches("[data-slot]")) {
    return;
  }
  const raw = event.dataTransfer?.getData(DRAG_TYPE);
  if (!raw) {
    return;
  }
  event.preventDefault();
  const { key, label } = JSON.parse(raw);
  fill(target, key, label);
}

export function init() {
  const root = form();
  if (!root) {
    return;
  }
  document.addEventListener("click", onChipClick);
  document.addEventListener("change", onSiteChange);
  // Поля без имени (отбор показателей, поиск перечня) формулу не меняют: предпросмотр
  // по их change не запрашивается.
  root.addEventListener(
    "change",
    (event) => {
      if (event.target instanceof HTMLInputElement && !event.target.name) {
        event.stopPropagation();
      }
    },
    true
  );
  root.addEventListener("dragstart", onDragStart);
  root.addEventListener("dragover", onDragOver);
  root.addEventListener("dragleave", onDragLeave);
  root.addEventListener("drop", onDrop);
  root.querySelectorAll("[data-slot]").forEach((slot) => {
    slot.addEventListener("focusin", () => markActive(slot));
    slot.addEventListener("click", () => markActive(slot));
  });
}
