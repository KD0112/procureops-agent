class HarnessError(RuntimeError):
    """Base class for fail-closed harness errors."""


class AuthorizationDenied(HarnessError):
    pass


class ApprovalRequired(HarnessError):
    pass


class ProhibitedAction(HarnessError):
    pass


class IdempotencyConflict(HarnessError):
    pass


class BudgetExceeded(HarnessError):
    pass


class DeadlineExceeded(HarnessError):
    pass


class ToolNotFound(HarnessError):
    pass


class TransientToolError(HarnessError):
    """A classified error that may be retried within the declared bound."""


class PermanentToolError(HarnessError):
    """A business or validation error that must not be retried."""

