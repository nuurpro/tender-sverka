"""Vendor-neutral SMS boundary. No provider payloads or secrets escape errors."""
from typing import Protocol

import httpx


class SmsSenderProtocol(Protocol):
    def send(self, phone: str, code: str) -> None: ...


class SmsUnavailable(Exception):
    pass


class HttpSmsSender:
    def __init__(self, endpoint, token, *, transport=None):
        self.endpoint = endpoint
        self.token = token
        self.transport = transport

    def send(self, phone, code):
        if not self.endpoint or not self.token or not self.token.get_secret_value():
            raise SmsUnavailable("SMS-сервис не настроен. Обратитесь к администратору.")
        try:
            with httpx.Client(timeout=10.0, transport=self.transport, follow_redirects=False) as client:
                response = client.post(self.endpoint,
                    headers={"Authorization": "Bearer " + self.token.get_secret_value()},
                    json={"phone": phone, "message": f"Код входа в Tender: {code}. Действует 5 минут."})
                if not 200 <= response.status_code < 300:
                    raise SmsUnavailable("Не удалось отправить SMS. Повторите позже.")
        except httpx.HTTPError:
            raise SmsUnavailable("Не удалось отправить SMS. Повторите позже.") from None
