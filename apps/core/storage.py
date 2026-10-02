"""Хранилище статики рабочего стенда."""

from whitenoise.storage import CompressedManifestStaticFilesStorage


class ModuleAwareStaticFilesStorage(CompressedManifestStaticFilesStorage):
    """
    Сжатая статика с отпечатками, переписывающая адреса в ES-модулях.

    Иначе модуль с отпечатком загружал бы соседей по именам без отпечатка, и сразу
    после выкладки страница могла бы собраться из нового и старых модулей.
    """

    # Импорты модулей не должны образовывать цикл: на цикле сборка статики обрывается.
    support_js_module_import_aggregation = True
