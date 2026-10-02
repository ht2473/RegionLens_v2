"""
Проверки обратной связи: приём обращения, ответ письмом, закрытие без ответа.

Форма обращения открыта для гостя, поэтому отдельно проверяются защиты: ловушка
для автоматических отправителей и ограничение числа обращений с одного адреса.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core import mail
from django.test import Client
from django.urls import reverse

from apps.feedback.constants import (
    RATE_LIMIT_PER_HOUR,
    DeliveryStatus,
    TicketStatus,
    TicketTopic,
)
from apps.feedback.models import Ticket

pytestmark = pytest.mark.integration


def valid_payload(**overrides: Any) -> dict[str, Any]:
    """Заполненная форма обращения."""
    payload = {
        "contact_name": "Иванов Иван",
        "contact_email": "ivanov@example.com",
        "topic": TicketTopic.DATA_ERROR,
        "subject": "Расхождение значения ВРП за 2019 год",
        "body": "В карточке показателя значение отличается от публикации Росстата.",
        "consent": "on",
        "website": "",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def ticket(db: None) -> Ticket:
    """Принятое обращение без ответа."""
    return Ticket.objects.create(
        contact_name="Петров Пётр",
        contact_email="petrov@example.com",
        topic=TicketTopic.METHOD,
        subject="Вопрос по индексу Тейла",
        body="Почему декомпозиция ведётся по федеральным округам?",
    )


# ---------------------------------------------------------------------------------------
# Приём обращения
# ---------------------------------------------------------------------------------------


def test_guest_can_send_request(client: Client, db: None) -> None:
    """Обращение принимается без входа в систему."""
    response = client.post(reverse("feedback:create"), valid_payload(), follow=True)

    assert response.status_code == 200
    ticket = Ticket.objects.get()
    assert ticket.author is None
    assert ticket.status == TicketStatus.NEW
    assert ticket.contact_email == "ivanov@example.com"


def test_confirmation_page_shows_ticket_number(client: Client, db: None) -> None:
    """После отправки посетитель видит номер обращения."""
    client.post(reverse("feedback:create"), valid_payload())
    ticket = Ticket.objects.get()

    response = client.get(reverse("feedback:sent"))
    assert str(ticket.pk) in response.content.decode()


def test_authenticated_request_uses_account_details(member_client: Client, member: Any) -> None:
    """
    Для вошедшего пользователя имя и адрес берутся из учётной записи.

    Иначе обращение можно было бы отправить от чужого имени, и ответ ушёл бы
    не тому человеку.
    """
    member_client.post(
        reverse("feedback:create"),
        valid_payload(contact_name="Чужое имя", contact_email="someone@else.test"),
    )

    ticket = Ticket.objects.get()
    assert ticket.author == member
    assert ticket.contact_email == member.email
    assert ticket.contact_name == member.full_name


def test_honeypot_rejects_automated_submission(client: Client, db: None) -> None:
    """Заполненное поле-ловушка означает автоматическую отправку: обращение не принимается."""
    response = client.post(reverse("feedback:create"), valid_payload(website="http://spam.test"))

    assert response.status_code == 200
    assert Ticket.objects.count() == 0


def test_short_message_is_rejected(client: Client, db: None) -> None:
    """Слишком короткое сообщение не принимается: по нему нельзя ответить по существу."""
    client.post(reverse("feedback:create"), valid_payload(body="почему?"))
    assert Ticket.objects.count() == 0


def test_rate_limit_blocks_flood(client: Client, db: None) -> None:
    """
    С одного адреса за час принимается ограниченное число обращений.

    Открытая форма без ограничения быстро превращается в канал доставки
    нежелательных сообщений.
    """
    for index in range(RATE_LIMIT_PER_HOUR):
        client.post(
            reverse("feedback:create"),
            valid_payload(subject=f"Обращение {index}"),
        )

    assert Ticket.objects.count() == RATE_LIMIT_PER_HOUR

    response = client.post(reverse("feedback:create"), valid_payload(subject="Лишнее"))

    assert Ticket.objects.count() == RATE_LIMIT_PER_HOUR
    assert "слишком много" in response.content.decode()


def test_administrators_are_notified_about_new_request(
    client: Client, second_admin: Any, db: None
) -> None:
    """О новом обращении администраторы узнают письмом: иначе оно останется незамеченным."""
    client.post(reverse("feedback:create"), valid_payload())
    letters = [letter for letter in mail.outbox if second_admin.email in letter.to]
    assert letters
    assert "Расхождение" in letters[0].subject


# ---------------------------------------------------------------------------------------
# Ответ письмом
# ---------------------------------------------------------------------------------------

DEFAULT_ANSWER = "Декомпозиция ведётся по округам, потому что…"


def answer(client: Client, ticket: Ticket, text: str = DEFAULT_ANSWER) -> Any:
    """Отправить ответ из карточки обращения."""
    return client.post(ticket.manage_url, {"action": "answer", "answer": text})


def test_answer_is_sent_by_email(
    second_admin_client: Client, second_admin: Any, ticket: Ticket, mailoutbox: list[Any]
) -> None:
    """Ответ уходит письмом на адрес отправителя, и обращение отмечается отвеченным."""
    answer(second_admin_client, ticket)

    ticket.refresh_from_db()
    assert ticket.status == TicketStatus.ANSWERED
    assert ticket.answered_by == second_admin
    assert ticket.answered_at is not None
    assert ticket.answer.startswith("Декомпозиция")

    assert len(mailoutbox) == 1
    letter = mailoutbox[0]
    assert letter.to == ["petrov@example.com"]
    assert str(ticket.pk) in letter.subject
    # В письме и ответ, и само обращение: автор мог его забыть.
    assert "Декомпозиция ведётся по округам" in letter.body
    assert "Почему декомпозиция ведётся по федеральным округам?" in letter.body


def test_failed_email_keeps_answer(
    second_admin_client: Client, ticket: Ticket, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Неушедшее письмо не теряет ответ: он сохранён, а письмо помечено недоставленным."""

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise OSError("почтовый сервер недоступен")

    monkeypatch.setattr("apps.core.mail.send_mail", refuse)
    response = answer(second_admin_client, ticket, "Ответ, который не должен потеряться")

    ticket.refresh_from_db()
    assert response.status_code == 302
    assert ticket.status == TicketStatus.ANSWERED
    assert ticket.answer == "Ответ, который не должен потеряться"
    assert ticket.answer_delivery == DeliveryStatus.FAILED
    page = second_admin_client.get(ticket.manage_url).content.decode()
    assert "Ответ, который не должен потеряться" in page
    assert "Не доставлено" in page


def test_second_answer_is_refused(
    second_admin_client: Client, ticket: Ticket, mailoutbox: list[Any]
) -> None:
    """Ответ один: повторная отправка формы не шлёт второго письма."""
    answer(second_admin_client, ticket)
    answer(second_admin_client, ticket, "Второй ответ")

    ticket.refresh_from_db()
    assert "Второй ответ" not in ticket.answer
    assert len(mailoutbox) == 1


def test_empty_answer_is_rejected(
    second_admin_client: Client, ticket: Ticket, mailoutbox: list[Any]
) -> None:
    """Пустой ответ не отправляется."""
    answer(second_admin_client, ticket, "   ")

    ticket.refresh_from_db()
    assert ticket.status == TicketStatus.NEW
    assert mailoutbox == []


def test_ticket_can_be_closed_without_answer(
    second_admin_client: Client, ticket: Ticket, mailoutbox: list[Any]
) -> None:
    """Обращение без ответа закрывается молча: автор не получает письма."""
    second_admin_client.post(ticket.manage_url, {"action": "close"})

    ticket.refresh_from_db()
    assert ticket.status == TicketStatus.CLOSED
    assert mailoutbox == []


def test_closed_ticket_can_still_be_answered(
    second_admin_client: Client, ticket: Ticket, mailoutbox: list[Any]
) -> None:
    """Закрытое по ошибке обращение не потеряно: ответить на него можно позже."""
    ticket.status = TicketStatus.CLOSED
    ticket.save(update_fields=["status"])

    answer(second_admin_client, ticket)

    ticket.refresh_from_db()
    assert ticket.status == TicketStatus.ANSWERED
    assert len(mailoutbox) == 1


def test_waiting_tickets_are_listed_by_default(second_admin_client: Client, ticket: Ticket) -> None:
    """По умолчанию перечень показывает только обращения, ждущие ответа."""
    Ticket.objects.create(
        contact_name="Сидоров",
        contact_email="sidorov@example.com",
        subject="Уже отвеченное обращение",
        body="Текст обращения достаточной длины",
        status=TicketStatus.ANSWERED,
    )

    content = second_admin_client.get(reverse("dashboard:ticket-list")).content.decode()
    assert ticket.subject in content
    assert "Уже отвеченное обращение" not in content

    everything = second_admin_client.get(reverse("dashboard:ticket-list"), {"status": ""})
    assert "Уже отвеченное обращение" in everything.content.decode()


# ---------------------------------------------------------------------------------------
# Доступ
# ---------------------------------------------------------------------------------------


def test_own_tickets_are_listed_with_answer(member_client: Client, member: Any) -> None:
    """В кабинете — свои обращения; ответ виден в карточке обращения."""
    own = Ticket.objects.create(
        author=member,
        contact_name=member.full_name,
        contact_email=member.email,
        subject="Свой вопрос о методике",
        body="Как считается постоянный состав субъектов?",
        answer="Состав — субъекты со значениями во всех годах окна.",
        status=TicketStatus.ANSWERED,
    )
    content = member_client.get(reverse("feedback:mine")).content.decode()
    assert own.subject in content

    detail = member_client.get(own.cabinet_url).content.decode()
    assert "субъекты со значениями во всех годах окна" in detail


def test_guest_ticket_with_same_address_is_not_shown(
    member_client: Client, member: Any, ticket: Ticket
) -> None:
    """
    Обращение гостя с тем же адресом в кабинет не попадает.

    Адрес при регистрации не подтверждается: иначе по нему читалась бы чужая переписка.
    """
    ticket.contact_email = member.email
    ticket.save(update_fields=["contact_email"])

    assert ticket.subject not in member_client.get(reverse("feedback:mine")).content.decode()
    assert member_client.get(ticket.cabinet_url).status_code == 404


def test_foreign_ticket_is_not_found(member_client: Client, make_user: Any) -> None:
    """Чужое обращение по прямому адресу — «не найдено», а не отказ."""
    other = make_user(email="other@example.com")
    foreign = Ticket.objects.create(
        author=other,
        contact_name=other.full_name,
        contact_email=other.email,
        subject="Чужой вопрос",
        body="Текст чужого обращения достаточной длины.",
    )
    assert member_client.get(foreign.cabinet_url).status_code == 404


def test_confirmation_links_to_cabinet(member_client: Client) -> None:
    """После отправки из учётной записи — ссылка на обращение в кабинете."""
    member_client.post(reverse("feedback:create"), valid_payload())
    ticket = Ticket.objects.get()
    assert ticket.cabinet_url in member_client.get(reverse("feedback:sent")).content.decode()


def test_answer_goes_to_those_who_handle_tickets(
    client: Client, editor: Any, member: Any, mailoutbox: list[Any]
) -> None:
    """Извещение о новом обращении получают все с правом на обращения, в том числе редактор."""
    client.post(reverse("feedback:create"), valid_payload())
    recipients = {address for message in mailoutbox for address in message.to}
    assert editor.email in recipients
    assert member.email not in recipients


def test_user_cannot_touch_ticket_card(member_client: Client, ticket: Ticket) -> None:
    """Разбор обращений — только для администратора, и по адресу тоже."""
    assert member_client.get(ticket.manage_url).status_code == 403
    member_client.post(ticket.manage_url, {"action": "close"})
    ticket.refresh_from_db()
    assert ticket.status == TicketStatus.NEW
