from enum import StrEnum


class TaskStatus(StrEnum):
    DRAFT = "draft"
    INGESTING = "ingesting"
    NEEDS_INPUT = "needs_input"
    MATCHING = "matching"
    SOURCING = "sourcing"
    CALCULATING = "calculating"
    RISK_REVIEW = "risk_review"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    PO_DRAFTED = "po_drafted"
    COMPLETED = "completed"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"


class RiskLevel(StrEnum):
    R0_READ_ONLY = "r0_read_only"
    R1_INTERNAL_DRAFT = "r1_internal_draft"
    R2_EXTERNAL_REVERSIBLE = "r2_external_reversible"
    R3_FINANCIAL_OR_LEGAL = "r3_financial_or_legal"
    R4_PROHIBITED = "r4_prohibited"


class ActionKind(StrEnum):
    READ = "read"
    WRITE_DRAFT = "write_draft"
    EXTERNAL_WRITE = "external_write"
    FINANCIAL = "financial"
    DESTRUCTIVE = "destructive"

