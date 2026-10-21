"""Rule registry and result types shared by every deterministic check.

Each rule has a stable id, a version, a default severity and a plain-language purpose.
ACCOUNTING_RULES.md is generated from the same registry (see `rules_catalogue`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Status(str, Enum):
    PASS = "pass"
    WARNING = "warning"
    ERROR = "error"
    NEEDS_REVIEW = "needs_review"
    NOT_APPLICABLE = "not_applicable"


BLOCKING = {Status.ERROR, Status.NEEDS_REVIEW}


@dataclass(frozen=True)
class Rule:
    id: str
    version: str
    severity: str  # error | warning | review — the worst status the rule can emit
    title: str
    purpose: str


@dataclass
class RuleResult:
    rule_id: str
    rule_version: str
    status: Status
    message: str
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "status": self.status.value,
            "message": self.message,
            "evidence": self.evidence,
        }


REGISTRY: dict[str, Rule] = {}


def rule(id: str, version: str, severity: str, title: str, purpose: str) -> Rule:
    r = Rule(id, version, severity, title, purpose)
    REGISTRY[id] = r
    return r


def result(r: Rule, status: Status, message: str, **evidence) -> RuleResult:
    return RuleResult(r.id, r.version, status, message, evidence)


def worst(results: list[RuleResult]) -> Status:
    order = [Status.ERROR, Status.NEEDS_REVIEW, Status.WARNING, Status.PASS, Status.NOT_APPLICABLE]
    present = {r.status for r in results}
    for s in order:
        if s in present:
            return s
    return Status.NOT_APPLICABLE


def rules_catalogue() -> list[dict]:
    return [r.__dict__ for r in sorted(REGISTRY.values(), key=lambda r: r.id)]
