from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from procureops.domain.models import ApprovalGrant


class ApprovalRequirement(BaseModel):
    model_config = ConfigDict(frozen=True)

    action: str
    required_roles: frozenset[str]
    total_amount: Decimal
    currency: str
    ruleset_version: str


class ProcurementPolicy:
    def __init__(self, *, rules: dict[str, object]) -> None:
        self.rules = rules

    @classmethod
    def from_file(cls, path: Path) -> ProcurementPolicy:
        return cls(rules=json.loads(path.read_text(encoding="utf-8")))

    def approval_requirement(
        self,
        *,
        total_amount: Decimal,
        currency: str,
        action: str,
    ) -> ApprovalRequirement:
        if currency != self.rules["currency"]:
            raise ValueError("unsupported currency for tenant ruleset")
        selected: frozenset[str] | None = None
        for threshold in self.rules["approval_thresholds"]:  # type: ignore[union-attr]
            minimum = Decimal(str(threshold["min_inclusive"]))
            raw_maximum = threshold["max_exclusive"]
            maximum = Decimal(str(raw_maximum)) if raw_maximum is not None else None
            if total_amount >= minimum and (maximum is None or total_amount < maximum):
                selected = frozenset(str(role) for role in threshold["required_roles"])
                break
        if selected is None:
            raise ValueError("no approval threshold matches total amount")
        return ApprovalRequirement(
            action=action,
            required_roles=selected,
            total_amount=total_amount,
            currency=currency,
            ruleset_version=str(self.rules["version"]),
        )

    @staticmethod
    def validate_grant_roles(
        grant: ApprovalGrant,
        requirement: ApprovalRequirement,
    ) -> None:
        missing = requirement.required_roles - grant.approved_by_roles
        if missing:
            raise PermissionError(
                f"approval is missing required approver roles: {sorted(missing)}"
            )
