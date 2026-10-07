"""Перечень публичных страниц для проверок — именами маршрутов, а не адресами."""

from __future__ import annotations

# Публичные страницы: доступны без входа в систему.
PUBLIC_PAGES = [
    "core:home",
    "core:about",
    "core:terms",
    "search:results",
    "catalog:indicator-list",
    "catalog:territory-list",
    "catalog:dataset",
    "catalog:sources",
    "maps:choropleth",
    "rankings:index",
    "compare:index",
    "surface:distribution",
    "surface:table",
    "analytics:index",
    "analytics:inequality",
    "analytics:convergence",
    "analytics:correlation",
    "analytics:spatial",
    "analytics:index-builder",
    "analytics:revisions",
    "api:docs",
    "userdata:index",
    "accounts:login",
    "accounts:register",
    "accounts:password-reset",
]
