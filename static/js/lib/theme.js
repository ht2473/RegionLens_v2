/* Тема оформления: «как в системе», светлая, тёмная. Переключатели и оповещение графиков
   и карты; до отрисовки тему применяет theme-init.js. */

const STORAGE_KEY = "regionlens:theme";

/** Событие на корне документа: оформление сменилось, цвета надо прочесть заново. */
export const THEME_CHANGED = "regionlens:theme-changed";

/** Прочитать сохранённый выбор пользователя. */
function stored() {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch (error) {
    // Хранилище может быть недоступно в приватном режиме.
    return null;
  }
}

/** Текущее действующее оформление с учётом системной настройки. */
function effective() {
  const explicit = document.documentElement.getAttribute("data-theme");
  if (explicit) {
    return explicit;
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** Привести переключатель в соответствие с оформлением; значок показывает действие. */
function reflect() {
  const next = effective() === "dark" ? "light" : "dark";
  document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
    const label = button.dataset[next === "dark" ? "labelDark" : "labelLight"];
    if (label) {
      button.setAttribute("aria-label", label);
      button.setAttribute("title", label);
    }
  });

  // Отмечается выбранное состояние, а не действующее.
  const chosen = stored() || "system";
  document.querySelectorAll("[data-theme-choice]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.themeChoice === chosen));
  });
}

/** Сообщить странице, что оформление сменилось. */
function announce(value) {
  document.documentElement.dispatchEvent(new CustomEvent(THEME_CHANGED, { detail: { value } }));
}

/** Применить тему к документу. */
function apply(value) {
  const root = document.documentElement;
  if (value === "light" || value === "dark") {
    root.setAttribute("data-theme", value);
  } else {
    root.removeAttribute("data-theme");
  }
  reflect();
  announce(value);
}

/** Сохранить и применить выбор. */
function set(value) {
  try {
    if (value === "system") {
      window.localStorage.removeItem(STORAGE_KEY);
    } else {
      window.localStorage.setItem(STORAGE_KEY, value);
    }
  } catch (error) {
    /* Сохранение необязательно: тема применится хотя бы на текущей странице. */
  }
  apply(value);
}

/**
 * Подписаться на смену оформления.
 *
 * @param {() => void} callback что сделать после смены
 */
export function onThemeChange(callback) {
  document.documentElement.addEventListener(THEME_CHANGED, callback);
}

/** Подключить переключатели темы. */
export function init() {
  document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
    button.addEventListener("click", () => set(effective() === "dark" ? "light" : "dark"));
  });
  document.querySelectorAll("[data-theme-choice]").forEach((button) => {
    button.addEventListener("click", () => set(button.dataset.themeChoice));
  });
  reflect();

  // При «как в системе» страница следует за системой без перезагрузки.
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (!stored()) {
      reflect();
      announce("system");
    }
  });
}
