"""
Конвейер сборки склада из исходного набора Росстата.

Этапы: ``source``, ``classify``, ``dimensions``, ``facts``, ``releases`` (выпуски внешних
источников), ``marts`` и ``catalog_sync``
(перенос в PostgreSQL); порядок задаёт ``pipeline``.
"""
