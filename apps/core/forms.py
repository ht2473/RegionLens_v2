"""Доступ к полям формы с проверкой их вида."""

from __future__ import annotations

from django import forms


def field_of[FieldType: forms.Field](
    form: forms.BaseForm, name: str, kind: type[FieldType]
) -> FieldType:
    """
    Вернуть поле формы, проверив его вид.

    В отличие от ``cast``, замена поля в форме даёт ошибку здесь, а не дальше по коду.
    """
    field = form.fields.get(name)
    if field is None:
        raise TypeError(f"Поле «{name}» отсутствует в форме {type(form).__name__}")
    if not isinstance(field, kind):
        raise TypeError(
            f"Поле «{name}» формы {type(form).__name__} имеет вид "
            f"{type(field).__name__}, ожидается {kind.__name__}"
        )
    return field
