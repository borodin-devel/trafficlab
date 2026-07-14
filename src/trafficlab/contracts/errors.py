"""Ошибки проверки файловых контрактов Trafficlab."""


class ContractError(ValueError):
    """Базовая ошибка нарушения архитектурного контракта."""


class InvariantError(ContractError):
    """Ошибка нарушения инварианта контракта."""
