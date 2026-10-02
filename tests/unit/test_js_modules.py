"""
Граф ES-модулей клиента вместо проверок сборщика: адреса import ведут к файлам, циклов нет
(на цикле сборка статики обрывается), каждый элемент из шаблонов есть в реестре app.js.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "static" / "js"
ENTRY = SCRIPTS / "app.js"

# Те же виды ссылок, что переписывает хранилище статики (support_js_module_import_aggregation).
IMPORT = re.compile(
    r"""(?:\bimport\b[^;'"()]*?\bfrom\s*|\bexport\b[^;'"()]*?\bfrom\s*|\bimport\s*\(\s*|\bimport\s*)"""
    r"""["'](?P<url>\.{1,2}/[^"']+)["']"""
)
COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)
REGISTRY = re.compile(
    r"""["'](?P<tag>rl-[a-z-]+)["']\s*:\s*\(\)\s*=>\s*"""
    r"""import\(["'](?P<url>[^"']+)["']\)"""
)
TEMPLATE_TAG = re.compile(r"<(rl-[a-z-]+)[\s>]")

pytestmark = pytest.mark.unit


def _modules() -> list[Path]:
    return sorted(path for path in SCRIPTS.rglob("*.js") if path.name != "theme-init.js")


def _imports(path: Path) -> list[Path]:
    """Модули, на которые ссылается файл; комментарии не в счёт."""
    code = COMMENT.sub("", path.read_text(encoding="utf-8"))
    return [(path.parent / match.group("url")).resolve() for match in IMPORT.finditer(code)]


def test_every_import_points_to_a_file() -> None:
    """Каждый относительный адрес модуля ведёт к существующему файлу."""
    missing = [
        f"{path.relative_to(ROOT)} → {target.name}"
        for path in _modules()
        for target in _imports(path)
        if not target.is_file()
    ]
    assert not missing


def test_module_graph_has_no_cycles() -> None:
    """В графе модулей нет циклов."""
    graph = {path.resolve(): _imports(path) for path in _modules()}
    state: dict[Path, str] = {}
    cycles: list[str] = []

    def visit(node: Path, trail: list[Path]) -> None:
        state[node] = "в пути"
        for target in graph.get(node, []):
            if state.get(target) == "в пути":
                cycles.append(" → ".join(item.name for item in [*trail, node, target]))
            elif target not in state:
                visit(target, [*trail, node])
        state[node] = "пройден"

    for node in graph:
        if node not in state:
            visit(node, [])
    assert not cycles


def test_every_module_is_reachable_from_the_entry() -> None:
    """Модулей, до которых не дотягивается входной, нет: их никто бы не загрузил."""
    reached: set[Path] = set()
    queue = [ENTRY.resolve()]
    while queue:
        node = queue.pop()
        if node in reached:
            continue
        reached.add(node)
        queue.extend(_imports(node))
    orphans = [path.relative_to(ROOT) for path in _modules() if path.resolve() not in reached]
    assert not orphans


def test_elements_in_templates_are_registered_and_defined() -> None:
    """Элемент из шаблона есть в реестре app.js, и его модуль его определяет."""
    registry = {
        match.group("tag"): (SCRIPTS / match.group("url")).resolve()
        for match in REGISTRY.finditer(ENTRY.read_text(encoding="utf-8"))
    }
    assert registry, "реестр элементов в app.js не найден"

    used = {
        tag
        for folder in (ROOT / "templates", ROOT / "apps")
        for template in folder.rglob("*.html")
        for tag in TEMPLATE_TAG.findall(template.read_text(encoding="utf-8"))
    }
    assert used - registry.keys() == set()

    for tag, module in registry.items():
        code = module.read_text(encoding="utf-8")
        assert f'customElements.define("{tag}"' in code, f"{module.name} не определяет <{tag}>"
