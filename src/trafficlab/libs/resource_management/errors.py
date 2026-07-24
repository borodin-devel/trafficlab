"""Typed failures for deterministic resource admission."""


class ResourceManagementError(Exception):
    """Base class for resource-management failures."""


class InvalidResourceValueError(ResourceManagementError):
    """A budget, observation, reservation, or decision value is invalid."""


class InconsistentLedgerError(ResourceManagementError):
    """A supplied ledger state violates conserved resource bounds."""
