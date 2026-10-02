/* Применение выбранной темы до отрисовки, без вспышки светлого фона; синхронно в head. */

(function () {
  var stored = null;
  try {
    stored = window.localStorage.getItem("regionlens:theme");
  } catch (error) {
    /* Локальное хранилище недоступно — остаётся системное оформление. */
  }
  if (stored === "light" || stored === "dark") {
    document.documentElement.setAttribute("data-theme", stored);
  }
})();
