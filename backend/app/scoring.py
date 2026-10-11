from .schemas import BidComparison, Requirement


def score(requirements: list[Requirement], result: BidComparison) -> dict:
    weights = {"mandatory": 3, "scored": 2, "desirable": 1}
    points = {"compliant": 10, "partial": 5, "unconfirmed": 3, "non_compliant": 0}
    expected = {r.id for r in requirements}
    rows = {r.requirement_id: r for r in result.rows}
    if (not requirements or len(expected) != len(requirements)
            or len(rows) != len(result.rows) or set(rows) != expected):
        raise ValueError("Нужен ровно один результат на каждое требование")
    total = pending = earned = review_count = 0
    risk = mandatory_review = False
    for requirement in requirements:
        weight = weights[requirement.importance]
        status = rows[requirement.id].status
        total += weight
        if status == "review":
            pending += weight
            review_count += 1
            mandatory_review |= requirement.importance == "mandatory"
            continue
        earned += weight * points[status]
        risk |= requirement.importance == "mandatory" and status != "compliant"
    known = total - pending
    return {
        "percent": round(10 * earned / known, 1) if known else None,
        "min_percent": round(10 * earned / total, 1),
        "max_percent": round(10 * (earned + 10 * pending) / total, 1),
        "risk": risk,
        "mandatory_review": mandatory_review,
        "review_count": review_count,
    }
