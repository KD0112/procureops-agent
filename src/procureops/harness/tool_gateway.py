from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from procureops.domain.enums import ActionKind, RiskLevel
from procureops.domain.models import ApprovalGrant, RunContext, canonical_hash
from procureops.harness.audit import AuditEvent, AuditSink
from procureops.harness.budget import RunBudgetLedger
from procureops.harness.errors import (
    ApprovalRequired,
    AuthorizationDenied,
    DeadlineExceeded,
    PermanentToolError,
    ProhibitedAction,
    ToolNotFound,
    TransientToolError,
)
from procureops.harness.idempotency import InMemoryIdempotencyStore


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    handler: Callable[[Mapping[str, Any]], Any]
    risk_level: RiskLevel
    action_kind: ActionKind
    required_any_roles: frozenset[str] = frozenset()
    max_attempts: int = 1
    tenant_argument: str | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("tool name is required")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.action_kind != ActionKind.READ and self.max_attempts > 1:
            raise ValueError("write tools cannot enable gateway retries")

    @property
    def is_write(self) -> bool:
        return self.action_kind != ActionKind.READ

    @property
    def requires_approval(self) -> bool:
        return self.risk_level in {
            RiskLevel.R2_EXTERNAL_REVERSIBLE,
            RiskLevel.R3_FINANCIAL_OR_LEGAL,
        }


class ToolExecutionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    tool_name: str
    output: Any
    attempts: int = Field(ge=0)
    idempotency_hit: bool = False


class ToolGateway:
    def __init__(
        self,
        *,
        audit: AuditSink,
        idempotency: InMemoryIdempotencyStore | None = None,
    ) -> None:
        self.audit = audit
        self.idempotency = idempotency or InMemoryIdempotencyStore()
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def execute(
        self,
        *,
        context: RunContext,
        ledger: RunBudgetLedger,
        tool_name: str,
        arguments: Mapping[str, Any],
        approval: ApprovalGrant | None = None,
        idempotency_key: str | None = None,
    ) -> ToolExecutionResult:
        tool = self._tools.get(tool_name)
        if tool is None:
            raise ToolNotFound(tool_name)
        request_payload = {"tool_name": tool_name, "arguments": dict(arguments)}
        request_hash = canonical_hash(request_payload)

        self._authorize(context=context, tool=tool)
        if tool.tenant_argument is not None:
            argument_tenant = arguments.get(tool.tenant_argument)
            if argument_tenant != context.tenant_id:
                raise AuthorizationDenied(
                    f"tool tenant argument does not match run context: {tool.name}"
                )
        self._check_approval(
            context=context,
            tool=tool,
            arguments=arguments,
            approval=approval,
        )
        if tool.is_write and not idempotency_key:
            raise ValueError("write tools require an idempotency key")

        def operation() -> ToolExecutionResult:
            return self._execute_with_retry(
                context=context,
                ledger=ledger,
                tool=tool,
                arguments=arguments,
                payload_hash=request_hash,
            )

        if tool.is_write:
            result, hit = self.idempotency.execute_once(
                key=str(idempotency_key),
                request_hash=request_hash,
                operation=operation,
            )
            if hit:
                self.audit.append(
                    AuditEvent.from_context(
                        context,
                        "tool.idempotency_hit",
                        payload_hash=request_hash,
                        metadata={"tool_name": tool_name},
                    )
                )
                return result.model_copy(update={"idempotency_hit": True})
            return result
        return operation()

    def _authorize(self, *, context: RunContext, tool: ToolDefinition) -> None:
        if context.is_expired():
            raise DeadlineExceeded("run deadline exceeded before tool call")
        if tool.risk_level == RiskLevel.R4_PROHIBITED:
            raise ProhibitedAction(f"tool is prohibited: {tool.name}")
        if tool.required_any_roles and not context.actor_roles.intersection(
            tool.required_any_roles
        ):
            raise AuthorizationDenied(f"actor lacks a required role for tool: {tool.name}")

    @staticmethod
    def _check_approval(
        *,
        context: RunContext,
        tool: ToolDefinition,
        arguments: Mapping[str, Any],
        approval: ApprovalGrant | None,
    ) -> None:
        if not tool.requires_approval:
            return
        if approval is None or not approval.authorizes(
            context=context,
            action=tool.name,
            subject=dict(arguments),
        ):
            raise ApprovalRequired(f"valid approval required for tool: {tool.name}")

    def _execute_with_retry(
        self,
        *,
        context: RunContext,
        ledger: RunBudgetLedger,
        tool: ToolDefinition,
        arguments: Mapping[str, Any],
        payload_hash: str,
    ) -> ToolExecutionResult:
        attempts = 0
        self.audit.append(
            AuditEvent.from_context(
                context,
                "tool.started",
                payload_hash=payload_hash,
                metadata={
                    "tool_name": tool.name,
                    "risk_level": tool.risk_level,
                    "action_kind": tool.action_kind,
                },
            )
        )
        while attempts < tool.max_attempts:
            attempts += 1
            ledger.charge_tool_call()
            try:
                output = tool.handler(arguments)
            except TransientToolError as exc:
                if attempts >= tool.max_attempts:
                    self._audit_failure(context, tool, payload_hash, attempts, exc)
                    raise
                self.audit.append(
                    AuditEvent.from_context(
                        context,
                        "tool.retrying",
                        payload_hash=payload_hash,
                        metadata={
                            "tool_name": tool.name,
                            "attempt": attempts,
                            "error_type": type(exc).__name__,
                        },
                    )
                )
                continue
            except PermanentToolError as exc:
                self._audit_failure(context, tool, payload_hash, attempts, exc)
                raise
            except Exception as exc:
                self._audit_failure(context, tool, payload_hash, attempts, exc)
                message = f"unclassified tool failure: {type(exc).__name__}"
                raise PermanentToolError(message) from exc

            result = ToolExecutionResult(
                tool_name=tool.name,
                output=output,
                attempts=attempts,
            )
            self.audit.append(
                AuditEvent.from_context(
                    context,
                    "tool.succeeded",
                    payload_hash=payload_hash,
                    metadata={"tool_name": tool.name, "attempts": attempts},
                )
            )
            return result
        raise AssertionError("unreachable retry state")

    def _audit_failure(
        self,
        context: RunContext,
        tool: ToolDefinition,
        payload_hash: str,
        attempts: int,
        error: Exception,
    ) -> None:
        self.audit.append(
            AuditEvent.from_context(
                context,
                "tool.failed",
                payload_hash=payload_hash,
                metadata={
                    "tool_name": tool.name,
                    "attempts": attempts,
                    "error_type": type(error).__name__,
                },
            )
        )
