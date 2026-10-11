import pytest


def requirement(id="R-001", importance="mandatory"):
    from app.schemas import Requirement
    return Requirement(id=id, scope="product", item=None, parameter="RAM",
                       condition="RAM >= 16 GB", importance=importance,
                       citations=[{"block_id": "t:p1", "quote": "RAM >= 16 GB"}])


def comparison(status="compliant", id="R-001", citations=None):
    from app.schemas import BidComparison
    return BidComparison(rows=[{"requirement_id": id, "status": status,
                                "explanation": "Evidence", "citations": citations or []}])


@pytest.mark.parametrize("status,percent,risk", [
    ("compliant", 100, False), ("partial", 50, True),
    ("unconfirmed", 30, True), ("non_compliant", 0, True),
])
def test_mandatory_failure_is_not_hidden_by_percentage(status, percent, risk):
    from app.scoring import score
    result = score([requirement()], comparison(status))
    assert result["percent"] == percent
    assert result["risk"] is risk


def test_all_review_has_no_percentage_or_ready_verdict():
    from app.scoring import score
    result = score([requirement()], comparison("review"))
    assert result["percent"] is None
    assert result["mandatory_review"] is True
    assert (result["min_percent"], result["max_percent"]) == (0, 100)


def test_weighted_score_and_unknown_range():
    from app.schemas import BidComparison
    from app.scoring import score
    kinds = ["mandatory", "mandatory", "scored", "desirable", "mandatory"]
    statuses = ["compliant", "non_compliant", "partial", "unconfirmed", "review"]
    reqs = [requirement(f"R-{i}", kind) for i, kind in enumerate(kinds)]
    rows = [comparison(status, f"R-{i}").rows[0] for i, status in enumerate(statuses)]
    result = score(reqs, BidComparison(rows=rows))
    assert result["percent"] == 47.8
    assert result["min_percent"] == 35.8
    assert result["max_percent"] == 60.8


def test_missing_or_duplicate_row_rejected():
    from app.schemas import BidComparison
    from app.scoring import score
    with pytest.raises(ValueError):
        score([requirement()], BidComparison(rows=[]))
    with pytest.raises(ValueError):
        score([requirement()], BidComparison(rows=comparison().rows * 2))


def test_unknown_or_fabricated_citation_cannot_be_green():
    from app.evidence import validate_comparison
    blocks = [{"id": "bid:p1", "text": "RAM 8 GB", "filename": "offer.pdf", "location": "Страница 1", "page": 1}]
    for block_id, quote in [("other:p1", "RAM 8 GB"), ("bid:p1", "RAM 32 GB")]:
        result = validate_comparison([requirement()], comparison(citations=[{"block_id": block_id, "quote": quote}]), blocks)
        assert result.rows[0].status == "review"
        assert result.rows[0].citations == []


def test_valid_whitespace_normalized_citation_is_preserved():
    from app.evidence import validate_comparison
    blocks = [{"id": "bid:p1", "text": "RAM\n 16 GB", "filename": "offer.pdf", "location": "Страница 1", "page": 1}]
    result = validate_comparison([requirement()], comparison(citations=[{"block_id": "bid:p1", "quote": "RAM 16 GB"}]), blocks)
    assert result.rows[0].status == "compliant"


def test_empty_citation_is_not_a_valid_substring():
    from app.evidence import validate_comparison
    result = validate_comparison([requirement()], comparison(citations=[{"block_id": "b:p1", "quote": "  "}]), [{"id": "b:p1", "text": "RAM 16 GB"}])
    assert result.rows[0].status == "review"


def test_evidence_is_required_for_compliant_and_contradiction():
    from app.evidence import validate_comparison
    for status in ["compliant", "partial", "non_compliant"]:
        assert validate_comparison([requirement()], comparison(status), []).rows[0].status == "review"
    assert validate_comparison([requirement()], comparison("unconfirmed"), []).rows[0].status == "unconfirmed"


def test_approval_rejects_invalid_sources():
    from app.evidence import validate_requirements
    with pytest.raises(ValueError):
        validate_requirements([requirement()], [{"id": "t:p1", "text": "different text"}])


def test_structured_outputs_require_every_property():
    from app.schemas import ChecklistExtraction, BidComparison
    for cls in [ChecklistExtraction, BidComparison]:
        schema = cls.model_json_schema()
        for definition in [schema, *schema.get("$defs", {}).values()]:
            if definition.get("type") == "object":
                assert definition["additionalProperties"] is False
                assert set(definition["required"]) == set(definition["properties"])


def test_formula_placeholder_cannot_prove_compliance():
    from app.evidence import validate_comparison
    quote = "B1: [Формула не обработана]"
    result = validate_comparison([requirement()], comparison(citations=[{"block_id": "b:s1:r1", "quote": quote}]), [{"id": "b:s1:r1", "text": quote}])
    assert result.rows[0].status == "review"
