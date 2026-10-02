/* Ползунок года рабочей поверхности <rl-time-slider>: подпись года и воспроизведение;
   за каждым годом уходит обычный запрос. */

// Задержка между кадрами воспроизведения.
const FRAME_DELAY = 1100;

// Таймер — вне элемента: рейль может прийти заново посреди воспроизведения.
let playbackTimer = null;

/**
 * Задать кнопке подпись и подсказку по её назначению; значок меняет оформление.
 *
 * @param {HTMLElement} button кнопка воспроизведения
 * @param {boolean} playing идёт ли перебор
 */
function reflect(button, playing) {
  button.classList.toggle("is-playing", playing);
  button.setAttribute("aria-pressed", playing ? "true" : "false");
  const label = playing ? button.dataset.labelStop : button.dataset.labelPlay;
  if (label) {
    button.setAttribute("aria-label", label);
    button.setAttribute("title", label);
  }
}

/** Остановить воспроизведение. */
function stop() {
  if (playbackTimer !== null) {
    window.clearInterval(playbackTimer);
    playbackTimer = null;
  }
  document.querySelectorAll("rl-time-slider [data-time-play]").forEach((button) => {
    reflect(button, false);
  });
}

/** Запустить перебор годов; ползунок ищется заново на каждом кадре. */
function start() {
  document.querySelectorAll("rl-time-slider [data-time-play]").forEach((button) => {
    reflect(button, true);
  });

  playbackTimer = window.setInterval(() => {
    const range = document.querySelector("rl-time-slider .time-slider__range");
    if (!range) {
      stop();
      return;
    }

    const next = Number(range.value) + 1;
    range.value = next > Number(range.max) ? range.min : String(next);
    // Событие input обновляет подпись года, событие change отправляет запрос.
    range.dispatchEvent(new Event("input", { bubbles: true }));
    range.dispatchEvent(new Event("change", { bubbles: true }));

    if (Number(range.value) >= Number(range.max)) {
      stop();
    }
  }, FRAME_DELAY);
}

class TimeSlider extends HTMLElement {
  connectedCallback() {
    if (this.bound) {
      return;
    }
    const range = this.querySelector(".time-slider__range");
    const value = this.querySelector(".time-slider__value");
    const button = this.querySelector("[data-time-play]");
    if (!range) {
      return;
    }
    this.bound = true;

    // Подпись — при перетаскивании: рейль при смене года не заменяется.
    range.addEventListener("input", () => {
      if (value) {
        value.textContent = range.value;
      }
    });

    if (button) {
      // Состояние новой кнопки — по общему таймеру.
      reflect(button, playbackTimer !== null);
      button.addEventListener("click", () => (playbackTimer !== null ? stop() : start()));
    }
  }
}

// Страница из кэша переходов не должна вернуться с идущим перебором.
window.addEventListener("pagehide", stop);

if (!customElements.get("rl-time-slider")) {
  customElements.define("rl-time-slider", TimeSlider);
}
