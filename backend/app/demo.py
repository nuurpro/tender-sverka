"""Explicit educational provider; never used as a fallback for OpenAI."""
import re

from .evidence import normalized
from .schemas import BidComparison, ChecklistExtraction, Requirement

SPECS = [
    ("Оперативная память", "Оперативная память — не менее 16 ГБ.", "product", "mandatory", r"Оперативная память|RAM"),
    ("Накопитель", "Накопитель — не менее 512 ГБ.", "product", "mandatory", r"Накопитель|Storage"),
    ("Гарантия", "Гарантия — не менее 24 месяцев.", "product", "mandatory", r"Гарантия|Warranty"),
    ("Документы", "Документы — сертификат соответствия и авторизационное письмо.", "participant", "mandatory", r"Документы"),
    ("Срок поставки", "Срок поставки — не более 10 дней.", "product", "scored", r"Срок поставки|Delivery"),
    ("Сервисный центр", "Сервисный центр — на территории Узбекистана.", "participant", "desirable", r"Сервисный центр"),
]


def educational(blocks):
    if not any("DEMO-001" in b["text"] for b in blocks):
        raise ValueError("Учебный режим поддерживает только комплект DEMO-001. Для своих документов включите AI_MODE=openai и добавьте API-ключ.")


def extract(blocks: list[dict]) -> ChecklistExtraction:
    educational(blocks)
    requirements = []
    for parameter, condition, scope, importance, _ in SPECS:
        block = next((b for b in blocks if normalized(condition) in normalized(b["text"])), None)
        if block is None:
            raise ValueError("Загрузите полное учебное ТЗ DEMO-001")
        requirements.append({
            "scope": scope, "item": "Ноутбук" if scope == "product" else None,
            "parameter": parameter, "condition": condition, "importance": importance,
            "citations": [{"block_id": block["id"], "quote": condition}],
        })
    return ChecklistExtraction(requirements=requirements)


def compare(requirements: list[Requirement], blocks: list[dict]) -> BidComparison:
    educational(blocks)
    rows = []
    by_parameter = {s[0]: s for s in SPECS}
    for requirement in requirements:
        spec = by_parameter.get(requirement.parameter)
        if not spec:
            raise ValueError("Учебный режим поддерживает параметры из исходного DEMO-001")
        block = match = None
        pattern = rf"(?:{spec[4]})\s*:\s*[^|\n\r]+"
        for candidate in blocks:
            found = re.search(pattern, candidate["text"], re.IGNORECASE)
            if found:
                block, match = candidate, found
                break
        citations = [{"block_id": block["id"], "quote": match.group(0).strip()}] if match else []
        status = "unconfirmed"
        explanation = "Подтверждение этого условия в учебной заявке не найдено."
        quote = match.group(0).lower() if match else ""
        if match and requirement.parameter in {"Оперативная память", "Накопитель", "Гарантия", "Срок поставки"}:
            expected = re.findall(r"\d+(?:[.,]\d+)?", requirement.condition)
            actual = re.findall(r"\d+(?:[.,]\d+)?", quote)
            if len(expected) != 1:
                raise ValueError("В учебном режиме у числового условия должен быть один порог")
            if not actual:
                status, explanation = "review", "Точное значение не указано: необходимо уточнение участника."
            else:
                target, value = float(expected[0].replace(",", ".")), float(actual[0].replace(",", "."))
                if "не более" in requirement.condition:
                    fits = value <= target
                elif "не менее" in requirement.condition:
                    fits = value >= target
                else:
                    raise ValueError("В учебном режиме используйте «не менее» или «не более»")
                status = "compliant" if fits else "non_compliant"
                explanation = f"Заявленное значение {value:g} {'выполняет' if fits else 'нарушает'} условие: {requirement.condition}"
        elif match:
            if requirement.condition != spec[1]:
                raise ValueError("Для изменения смысловых условий подключите OpenAI")
            if requirement.parameter == "Документы":
                if "сертификат" in quote and "авторизационное письмо" in quote:
                    status, explanation = "compliant", "В учебном комплекте указаны оба требуемых документа."
                else:
                    status, explanation = "partial", "Указан только один из двух требуемых документов."
            elif requirement.parameter == "Сервисный центр":
                fits = "узбекистан" in quote or "ташкент" in quote
                status = "compliant" if fits else "non_compliant"
                explanation = "Сервисный центр находится в Узбекистане." if fits else "В заявке явно указано отсутствие сервисного центра."
        rows.append({"requirement_id": requirement.id, "status": status,
                     "explanation": explanation, "citations": citations})
    return BidComparison(rows=rows)
