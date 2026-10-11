import unicodedata

from .schemas import BidComparison, Citation, Requirement


def normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


def valid_citation(citation: Citation, blocks: dict[str, dict]) -> bool:
    block = blocks.get(citation.block_id)
    quote = normalized(citation.quote)
    return bool(block and quote and quote in normalized(block["text"]))


def validate_requirements(requirements: list[Requirement], blocks: list[dict]) -> None:
    if not requirements:
        raise ValueError("В чек-листе должно быть хотя бы одно требование")
    if len({r.id for r in requirements}) != len(requirements):
        raise ValueError("ID требований должны быть уникальными")
    index = {b["id"]: b for b in blocks}
    for requirement in requirements:
        if not requirement.id.strip() or not requirement.parameter.strip() or not requirement.condition.strip():
            raise ValueError("Название и условие требования не могут быть пустыми")
        if not requirement.citations or not all(valid_citation(c, index) for c in requirement.citations):
            raise ValueError(f"{requirement.id}: цитата не найдена в исходном документе")


def validate_comparison(
    requirements: list[Requirement], result: BidComparison, blocks: list[dict]
) -> BidComparison:
    expected = {r.id for r in requirements}
    ids = [r.requirement_id for r in result.rows]
    if len(set(ids)) != len(ids) or set(ids) != expected:
        raise ValueError("AI должен вернуть ровно одну строку на каждое требование")
    index = {b["id"]: b for b in blocks}
    rows = []
    for row in result.rows:
        verified = [c for c in row.citations if valid_citation(c, index)]
        invalid = len(verified) != len(row.citations)
        missing = not verified and row.status in {"compliant", "partial", "non_compliant"}
        formula = any("[Формула не обработана]" in c.quote for c in verified)
        if invalid or missing or formula:
            row = row.model_copy(update={
                "status": "review",
                "citations": verified,
                "explanation": "Требуется проверка: AI не предоставил проверяемую цитату из этой заявки.",
            })
        rows.append(row)
    return BidComparison(rows=rows)
