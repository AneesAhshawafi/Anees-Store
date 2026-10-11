from typing import Dict, List
from payments.base import BasePaymentProvider
from payments.binance_pay import BinancePayProvider

_PROVIDERS: Dict[str, BasePaymentProvider] = {}


def register_provider(provider: BasePaymentProvider):
    """Registers a payment provider in the central registry."""
    _PROVIDERS[provider.id] = provider


def get_provider(provider_id: str) -> BasePaymentProvider:
    """Returns a payment provider by its unique identifier."""
    return _PROVIDERS.get(provider_id)


def get_active_providers() -> List[BasePaymentProvider]:
    """Returns list of all configured and active payment providers."""
    return [p for p in _PROVIDERS.values() if p.is_configured()]


def get_all_providers() -> List[BasePaymentProvider]:
    """Returns all registered providers."""
    return list(_PROVIDERS.values())


# Register default providers
register_provider(BinancePayProvider())
