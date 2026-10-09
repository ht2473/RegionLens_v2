"""Проверка раскладки и бюджета страницы в браузере: общая для сквозных проверок и замеров."""

from __future__ import annotations

import gzip
from typing import Any

# Пределы правил раскладки.
MAX_LINE_CHARS = 80  # самая длинная строка абзаца, знаков: цель — 70, латиница уже кириллицы
MIN_EMPTY_HEIGHT = 200  # выше этого блок без содержания считается пустым, точек

# Скрипт возвращает нарушения раскладки и числа страницы. Правила:
# длинные строки текста; одинокое слово в последней строке заголовка; пояснение справа
# от заголовка и действие ниже его линии; блок выше предела без содержания; подпись,
# сжатая так, что строк больше, чем слов, и не меньше трёх.
LAYOUT_SCRIPT = r"""([maxLineChars, minEmptyHeight]) => {
    const main = document.querySelector('main') || document.body;
    const problems = [];

    const visible = (el) => {
        const r = el.getBoundingClientRect();
        if (r.width < 1 || r.height < 1) return false;
        const s = getComputedStyle(el);
        return s.visibility !== 'hidden' && s.display !== 'none';
    };
    const name = (el) => el.tagName.toLowerCase()
        + [...el.classList].slice(0, 2).map((c) => '.' + c).join('');
    const words = (s) => s.split(/\s+/).filter(Boolean);
    const lineHeight = (el) => {
        const s = getComputedStyle(el);
        const lh = parseFloat(s.lineHeight);
        return Number.isFinite(lh) ? lh : parseFloat(s.fontSize) * 1.3;
    };
    // Число строк — по верхним краям прямоугольников текста.
    const lineTops = (range, tolerance) => {
        const tops = [];
        for (const r of range.getClientRects()) {
            if (r.width < 1) continue;
            if (!tops.some((t) => Math.abs(t - r.top) < tolerance)) tops.push(r.top);
        }
        return tops.sort((a, b) => a - b);
    };
    const contentLines = (el) => {
        const range = document.createRange();
        range.selectNodeContents(el);
        return lineTops(range, lineHeight(el) / 2);
    };

    // 1. Длина строки абзаца: ширина самой длинной строки в средних знаках этого текста.
    const canvas = document.createElement('canvas').getContext('2d');
    main.querySelectorAll('p, li, dd, blockquote').forEach((el) => {
        if (!visible(el) || el.closest('table, pre, nav')) return;
        if (el.querySelector('p, ul, ol, div')) return;
        const text = el.innerText.replace(/\s+/g, ' ').trim();
        if (text.length < 120) return;
        const range = document.createRange();
        range.selectNodeContents(el);
        const tolerance = lineHeight(el) / 2;
        const lines = [];
        for (const r of range.getClientRects()) {
            if (r.width < 1) continue;
            const line = lines.find((l) => Math.abs(l.top - r.top) < tolerance);
            if (line) {
                line.left = Math.min(line.left, r.left);
                line.right = Math.max(line.right, r.right);
            } else {
                lines.push({ top: r.top, left: r.left, right: r.right });
            }
        }
        if (lines.length < 2) return;
        const style = getComputedStyle(el);
        canvas.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
        const charWidth = canvas.measureText(text).width / text.length;
        const widest = Math.max(...lines.map((l) => l.right - l.left));
        const perLine = Math.round(widest / charWidth);
        if (perLine > maxLineChars) {
            const sample = text.slice(0, 40);
            problems.push(`длинные строки (${perLine} знаков): ${name(el)} «${sample}…»`);
        }
    });

    // 2. Одинокое слово: последняя строка заголовка короче трети самой длинной.
    const titles = main.querySelectorAll('h1, h2, h3, [class*="__title"]');
    titles.forEach((el) => {
        if (!visible(el)) return;
        const text = el.innerText.replace(/\s+/g, ' ').trim();
        if (words(text).length < 3) return;
        const range = document.createRange();
        range.selectNodeContents(el);
        const tolerance = lineHeight(el) / 2;
        const lines = [];
        for (const r of range.getClientRects()) {
            if (r.width < 1) continue;
            const line = lines.find((l) => Math.abs(l.top - r.top) < tolerance);
            if (line) {
                line.left = Math.min(line.left, r.left);
                line.right = Math.max(line.right, r.right);
            } else {
                lines.push({ top: r.top, left: r.left, right: r.right });
            }
        }
        if (lines.length < 2) return;
        lines.sort((a, b) => a.top - b.top);
        const widths = lines.map((l) => l.right - l.left);
        if (widths[widths.length - 1] < Math.max(...widths) / 3) {
            problems.push(`одинокое слово в заголовке: ${name(el)} «${text.slice(0, 50)}»`);
        }
    });

    // 3. Блоки справа от заголовка: пояснение — под заголовком, действие — на его линии.
    const interactive = 'a[href], button, input, select, textarea, summary';
    main.querySelectorAll('h1, h2').forEach((heading) => {
        if (!visible(heading)) return;
        const box = heading.parentElement.closest(
            'header, [class*="__head"], [class*="-head"]') || heading.parentElement;
        const boxRect = box.getBoundingClientRect();
        if (boxRect.height > 600) return;
        const headRange = document.createRange();
        headRange.selectNodeContents(heading);
        const headRects = [...headRange.getClientRects()].filter((r) => r.width >= 1);
        if (!headRects.length) return;
        const headTop = Math.min(...headRects.map((r) => r.top));
        const headLine = lineHeight(heading);
        const flagged = [];
        const wrapper = [...box.children].find((child) => child.contains(heading));
        const inner = wrapper && wrapper !== heading ? [...wrapper.children] : [];
        const candidates = [...box.children, ...inner];
        candidates.forEach((el) => {
            if (el.contains(heading) || heading.contains(el) || !visible(el)) return;
            if (el.classList.contains('page-head__aside')) return;
            if (flagged.some((f) => f.contains(el))) return;
            const r = el.getBoundingClientRect();
            if (r.left < boxRect.left + boxRect.width * 0.45) return;
            const text = el.innerText.replace(/\s+/g, ' ').trim();
            const acts = el.matches(interactive) || el.querySelector(interactive);
            if (!acts && text) {
                problems.push(`пояснение справа от заголовка: ${name(el)} «${text.slice(0, 40)}»`);
                flagged.push(el);
            } else if (acts && Math.abs(r.top - headTop) > headLine) {
                problems.push(`действие вне линии заголовка: ${name(el)} «${text.slice(0, 40)}»`);
                flagged.push(el);
            }
        });
    });

    // 4. Пустой блок: высокий, почти без текста, без чисел, ссылок и без изображения,
    // графика, таблицы, поля. Свёрнутый до поиска текст (hidden="until-found") — содержание;
    // сообщение «пока пусто» (.empty-state) — не заглушка, а ответ.
    const media = 'img, svg:not([aria-hidden="true"]), canvas, table, input, select, textarea, '
        + 'iframe, video, pre, rl-chart, rl-choropleth, rl-live-map';
    const empty = [];
    main.querySelectorAll('div, section, aside, article, figure').forEach((el) => {
        if (!visible(el) || el.getBoundingClientRect().height <= minEmptyHeight) return;
        // Обёртка всей страницы — не заглушка: у короткого сообщения она просто невысока.
        if (el.parentElement === main) return;
        if (el.closest('.empty-state')) return;
        if (el.querySelector(media) || el.querySelectorAll(interactive).length >= 3) return;
        const shown = el.innerText.trim();
        if (!shown && el.textContent.trim()) return;
        if (/\d/.test(shown) || words(shown).length > 15) return;
        empty.push(el);
    });
    empty.filter((el) => !empty.some((other) => other !== el && el.contains(other)))
        .forEach((el) => {
            const h = Math.round(el.getBoundingClientRect().height);
            const text = el.innerText.replace(/\s+/g, ' ').trim();
            problems.push(`пустой блок ${h} точек: ${name(el)} «${text.slice(0, 40)}»`);
        });

    // 5. Подпись, сжатая до букв: строк больше, чем слов.
    const walker = document.createTreeWalker(main, NodeFilter.SHOW_TEXT);
    const squeezed = new Set();
    while (walker.nextNode()) {
        const node = walker.currentNode;
        const text = node.textContent.trim();
        const parent = node.parentElement;
        if (text.length < 4 || !parent || squeezed.has(parent) || !visible(parent)) continue;
        const range = document.createRange();
        range.selectNodeContents(node);
        const lines = lineTops(range, lineHeight(parent) / 2).length;
        // Части — слова и куски путей и ключей: такие переносятся по разделителям.
        const parts = text.split(/[\s\-‐–—\/\\_.]+/).filter(Boolean).length;
        if (lines >= 3 && lines > parts) {
            squeezed.add(parent);
            const sample = text.slice(0, 30);
            problems.push(`сжатая подпись (${lines} строк): ${name(parent)} «${sample}»`);
        }
    }

    // Числа страницы. Объём стилей — по разобранным правилам: размер из сведений
    // о загрузке у взятого из кэша файла равен нулю.
    const scripts = performance.getEntriesByType('resource')
        .filter((e) => new URL(e.name).pathname.endsWith('.js')).length;
    const cssChars = [...document.styleSheets].reduce((sum, sheet) => {
        try {
            return sum + [...sheet.cssRules].reduce((s, rule) => s + rule.cssText.length, 0);
        } catch (error) {
            return sum;
        }
    }, 0);
    const controls = [...main.querySelectorAll(interactive)].filter(visible).length;
    return {
        problems,
        metrics: {
            height: document.documentElement.scrollHeight,
            words: words(main.innerText).length,
            controls,
            nodes: document.getElementsByTagName('*').length,
            scripts,
            css_kb: Math.round(cssChars / 1024),
            // Документ, как он пришёл по сети: сжатый GZipMiddleware.
            html_kb: Math.round(
                (performance.getEntriesByType('navigation')[0]?.encodedBodySize || 0) / 1024,
            ),
        },
    };
}"""


def audit(page: Any) -> dict[str, Any]:
    """Нарушения раскладки и числа открытой страницы."""
    return page.evaluate(LAYOUT_SCRIPT, [MAX_LINE_CHARS, MIN_EMPTY_HEIGHT])


def html_kb(body: bytes) -> tuple[int, int]:
    """Размер HTML страницы, КБ: как есть и сжатый."""
    return round(len(body) / 1024), round(len(gzip.compress(body, 6)) / 1024)
