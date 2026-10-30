"""Пояснения к расчётам анализа видны на странице: на английской версии — по-английски."""

from __future__ import annotations

import re

import numpy as np
import pytest
from django.utils import translation

from apps.analytics.core import inequality, normalization, weighting

CYRILLIC = re.compile(r"[а-яё]", re.IGNORECASE)
STEADY = [5.0, 5.0, 5.0, 5.0, 5.0, 5.0]


@pytest.mark.parametrize(
    "note",
    [
        lambda: inequality.measure_set([-1.0, 2.0, 3.0, 4.0, 5.0, 6.0])["by_code"]["gini"].note,
        lambda: inequality.measure_set([0.0, 2.0, 3.0, 4.0, 5.0, 6.0])["by_code"]["theil"].note,
        lambda: normalization.normalize(STEADY, method="minmax").note,
        lambda: normalization.normalize(STEADY, method="zscore").note,
        lambda: normalization.normalize([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], method="rank").note,
        lambda: normalization.normalize([0.0, 2.0, 3.0, 4.0, 5.0, 6.0], method="log").note,
        lambda: normalization.normalize([1.0] * 30 + [900.0], method="zscore").note,
        lambda: weighting.build_weights(np.zeros((0, 0))).note,
        lambda: weighting.build_weights(np.ones((2, 3)), method="entropy").note,
        lambda: (
            weighting.build_weights(np.ones((8, 3)), method="expert", expert=[0.0, 0.0, 0.0]).note
        ),
        lambda: weighting.build_weights(np.ones((8, 3)), method="critic").note,
        lambda: (
            weighting.build_weights(np.random.default_rng(1).random((12, 3)), method="pca").note
        ),
    ],
)
def test_note_is_translated(note) -> None:
    """Пояснение есть и на английской странице не содержит русских букв."""
    with translation.override("ru"):
        assert str(note())
    with translation.override("en"):
        assert not CYRILLIC.search(str(note()))
