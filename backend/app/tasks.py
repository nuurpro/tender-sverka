import logging

from openai import APIConnectionError, APIStatusError, APITimeoutError

from . import ai
from .db import Store
from .evidence import validate_comparison, validate_requirements
from .parsers import parse_package
from .schemas import Requirement
from .scoring import score

logger = logging.getLogger(__name__)


def error_message(exc: Exception) -> str:
    service = ai.provider_name()
    if isinstance(exc, APITimeoutError):
        return f"{service} не ответил вовремя. Повторите обработку."
    if isinstance(exc, APIConnectionError):
        return f"Не удалось подключиться к {service}. Проверьте сеть и повторите."
    if isinstance(exc, APIStatusError):
        detail = exc.body if isinstance(exc.body, dict) else {}
        detail = detail.get("error", detail)
        if not isinstance(detail, dict):
            detail = {}
        if ai.is_agentrouter():
            if detail.get("code") == "content-blocked":
                return "AgentRouter заблокировал содержимое запроса (content-blocked). Проверьте ограничения шлюза или обратитесь в его поддержку."
            if "unauthorized client detected" in str(detail.get("message", "")).lower():
                return "AgentRouter не разрешил запрос этому API-клиенту. Проверьте доступ ключа к API приложения или обратитесь в поддержку шлюза."
        if exc.status_code == 401:
            return f"{service} отклонил API-ключ. Проверьте backend/.env."
        if exc.status_code == 429:
            return f"Достигнут лимит {service}. Проверьте квоту и повторите позже."
        protocol = "Chat Completions и JSON mode" if ai.api_style() == "chat" else "Responses API и Structured Outputs"
        return f"Ошибка {service} ({exc.status_code}). Проверьте модель и поддержку {protocol}."
    if isinstance(exc, ValueError):
        return str(exc)
    logger.exception("Processing failed", exc_info=exc)
    return "Не удалось завершить обработку. Повторите запуск."


def run_extraction(check_id: str, store: Store) -> None:
    try:
        store.update(check_id, status="running")
        check = store.get(check_id)
        blocks = parse_package(check["files"])
        # Preserve sources even if extraction fails, so errors remain inspectable.
        store.update(check_id, blocks=blocks)
        extraction = ai.extract_requirements(blocks)
        requirements = [
            Requirement(id=f"R-{number:03d}", **r.model_dump())
            for number, r in enumerate(extraction.requirements, 1)
        ]
        validate_requirements(requirements, blocks)
        store.update(check_id, requirements=[r.model_dump() for r in requirements],
                     results={}, approved=False, status="done", error=None)
    except Exception as exc:
        store.update(check_id, status="failed", error=error_message(exc))


def run_comparison(check_id: str, store: Store) -> None:
    try:
        store.update(check_id, status="running", results={})
        check = store.get(check_id)
        requirements = [Requirement.model_validate(r) for r in check["requirements"]]
        validate_requirements(requirements, check["blocks"])
        results = {}
        bids = check["bids"]
        for slot in sorted(bids, key=int):
            bid = bids[slot]
            blocks = parse_package(bid["files"])
            bid["blocks"] = blocks
            store.update(check_id, bids=bids)
            comparison = validate_comparison(requirements, ai.compare_bid(requirements, blocks), blocks)
            results[slot] = {"comparison": comparison.model_dump(), "score": score(requirements, comparison)}
            store.update(check_id, results=results)
        store.update(check_id, status="done", error=None)
    except Exception as exc:
        # Partial results may be shown, but cannot be exported as a finished matrix.
        store.update(check_id, status="failed", error=error_message(exc))
