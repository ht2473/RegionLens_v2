/* =====================================================================================
 * Точка сборки урезанной библиотеки графиков.
 *
 * Полный дистрибутив ECharts весит мегабайт и содержит географические карты, свечные
 * и водопадные диаграммы, лупу временной шкалы, панель инструментов и ещё три десятка
 * построений, ни одно из которых в работе не используется. Картограмма здесь рисуется
 * на сервере разметкой SVG, поэтому самая крупная часть — географический слой —
 * не нужна вовсе.
 *
 * Ниже перечислено ровно то, что встречается в настройках графиков, собираемых
 * в `apps/core/charts.py` и `apps/analytics/charts.py`. Добавили новое построение —
 * допишите его сюда и пересоберите; иначе график молча не отрисуется.
 *
 * Сборка одноразовая, в цикл разработки не входит; её результат лежит готовым файлом
 * в `static/vendor/echarts.min.js`. Как пересобрать — см. `scripts/echarts/README.md`.
 * ===================================================================================== */

import * as echarts from "echarts/core";

import {
  BarChart,
  HeatmapChart,
  LineChart,
  ScatterChart,
} from "echarts/charts";

import {
  AxisPointerComponent,
  GridComponent,
  LegendComponent,
  LegendScrollComponent,
  MarkLineComponent,
  TitleComponent,
  TooltipComponent,
  VisualMapContinuousComponent,
} from "echarts/components";

import { LabelLayout } from "echarts/features";
import { CanvasRenderer, SVGRenderer } from "echarts/renderers";

echarts.use([
  // Построения: линии динамики и мест, столбцы вкладов, точки рассеяния и полоса
  // распределения, матрица связей.
  LineChart,
  BarChart,
  ScatterChart,
  HeatmapChart,

  // Части холста: поле, оси-указатели подсказки, легенда (обычная и прокручиваемая),
  // заголовок, отметки разрывов сопоставимости и медианы, непрерывная шкала цвета матрицы.
  GridComponent,
  TooltipComponent,
  AxisPointerComponent,
  LegendComponent,
  LegendScrollComponent,
  TitleComponent,
  MarkLineComponent,
  VisualMapContinuousComponent,

  // Расталкивание сошедшихся подписей у концов линий.
  LabelLayout,

  // Отрисовка холстом на странице (тип задаётся явно при создании графика) и SVG —
  // только для картинки графика файлом SVG (static/js/elements/chart.js).
  CanvasRenderer,
  SVGRenderer,
]);

export * from "echarts/core";
