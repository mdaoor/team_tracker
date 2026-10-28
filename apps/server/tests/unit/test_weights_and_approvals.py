from __future__ import annotations

from decimal import Decimal

import pytest
from tt_schema.enums import ApprovalPolicy, ApprovalStatus, Decision
from tt_server.domain.errors import DomainError
from tt_server.domain.overlay.approvals import evaluate as eval_approval
from tt_server.domain.overlay.weights import evaluate, validate_formula

CTX = {
    "estimate_hours": Decimal(8),
    "points": Decimal(5),
    "priority": "high",
    "type": "bug",
    "impact": 4,
    "stage": "test",
    "tags": ["customer_facing"],
    "dimensions": {"client": ["acme"]},
}
DEFAULT = {
    "base": "constant",
    "multipliers": {"priority.high": 2, "priority.highest": 2.5, "priority.critical": 3},
}


def test_default_scheme_reproduces_the_seed_weights() -> None:
    for prio, want in {"low": 1, "medium": 1, "high": 2, "highest": 2.5, "critical": 3}.items():
        assert evaluate(DEFAULT, {**CTX, "priority": prio}) == Decimal(str(want)).quantize(Decimal("0.0001"))


def test_bases_and_missing_estimate() -> None:
    assert evaluate({"base": "estimate_hours"}, CTX) == Decimal("8.0000")
    assert evaluate({"base": "points"}, CTX) == Decimal("5.0000")
    assert evaluate({"base": "estimate_hours"}, {**CTX, "estimate_hours": None}) == Decimal("1.0000")
    assert evaluate({"base": "constant", "constant": 3}, CTX) == Decimal("3.0000")


def test_multipliers_compose_and_clamp() -> None:
    f = {
        "base": "estimate_hours",
        "multipliers": {
            "priority.high": 2,
            "stage.test": 1.5,
            "tag.customer_facing": 1.3,
            "type.bug": 0.5,
            "impact.4": 2,
            "dimension.client.acme": 3,
        },
    }
    assert evaluate(f, CTX) == Decimal(8) * 2 * Decimal("1.5") * Decimal("1.3") * Decimal("0.5") * 2 * 3
    assert evaluate({**f, "clamp": [1, 10]}, CTX) == Decimal("10.0000")
    assert evaluate({"base": "constant", "multipliers": {"priority.low": 9}}, CTX) == Decimal(
        "1.0000"
    )  # not applicable


@pytest.mark.parametrize(
    "formula",
    [
        {"base": "magic"},
        {"multipliers": {"nonsense.x": 2}},
        {"multipliers": {"priority": 2}},
        {"multipliers": {"priority.high": "lots"}},
        {"clamp": [5, 1]},
        {"clamp": [1]},
    ],
)
def test_invalid_formulas_are_rejected(formula: dict) -> None:  # type: ignore[type-arg]
    with pytest.raises(DomainError) as exc:
        validate_formula(formula)
    assert exc.value.code == "invalid_formula"


def test_valid_formula_passes() -> None:
    validate_formula(
        {"base": "points", "multipliers": {"dimension.client.acme": 1.2, "tag.x": 2}, "clamp": [0, 5]}
    )


A, R, P, X = Decision.APPROVED, Decision.REJECTED, Decision.PENDING, ApprovalStatus


@pytest.mark.parametrize(
    ("policy", "n", "decisions", "expected"),
    [
        (ApprovalPolicy.ANY_ONE, None, [P, P], X.PENDING),
        (ApprovalPolicy.ANY_ONE, None, [A, P], X.APPROVED),
        (ApprovalPolicy.ANY_ONE, None, [R, P], X.PENDING),
        (ApprovalPolicy.ANY_ONE, None, [R, R], X.REJECTED),
        (ApprovalPolicy.ALL, None, [A, P], X.PENDING),
        (ApprovalPolicy.ALL, None, [A, A], X.APPROVED),
        (ApprovalPolicy.ALL, None, [A, R], X.REJECTED),
        (ApprovalPolicy.QUORUM, 2, [A, P, P], X.PENDING),
        (ApprovalPolicy.QUORUM, 2, [A, A, P], X.APPROVED),
        (ApprovalPolicy.QUORUM, 2, [A, R, R], X.REJECTED),
        (ApprovalPolicy.QUORUM, 3, [A, A], X.APPROVED),  # quorum is capped at the number of approvers
    ],
)
def test_approval_policies(
    policy: ApprovalPolicy, n: int | None, decisions: list[Decision], expected: ApprovalStatus
) -> None:
    assert eval_approval(policy, n, decisions) == expected
