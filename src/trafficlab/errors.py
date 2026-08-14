"""Errors exposed by the trafficlab package."""


class TrafficlabError(Exception):
    """An expected trafficlab failure with a suggested corrective action."""

    def __init__(self, message: str, *, corrective_action: str, exit_code: int = 2) -> None:
        super().__init__(message)
        self.corrective_action = corrective_action
        self.exit_code = exit_code


class DeadlineExceededError(TrafficlabError):
    """A structured signal that an absolute operation deadline has expired."""
