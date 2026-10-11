from abc import ABC, abstractmethod


class BasePaymentProvider(ABC):
    """Abstract base class for all payment and deposit providers."""

    @property
    @abstractmethod
    def id(self) -> str:
        """Unique identifier of the provider (e.g. 'binance_pay', 'kuraimi')."""
        pass

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable display name in Arabic."""
        pass

    @property
    @abstractmethod
    def icon(self) -> str:
        """Emoji icon for buttons and messages."""
        pass

    @abstractmethod
    def is_configured(self) -> bool:
        """Returns True if the provider has necessary credentials and is active."""
        pass

    @abstractmethod
    def get_deposit_instructions(self, user_id: int) -> dict:
        """
        Returns instructions for the customer to make a deposit.
        Returns a dict:
        {
            "title": str,
            "text": str,
            "account_id": str,
            "currency": str,
            "min_amount": float,
            "prompt_for_input": str
        }
        """
        pass

    @abstractmethod
    def verify_deposit(self, user_id: int, proof_data: str) -> dict:
        """
        Verifies the customer's payment proof (order ID, transaction ID, hash, etc.).
        Returns a dict:
        {
            "verified": bool,
            "amount": float,
            "currency": str,
            "reference_id": str,
            "error": str (optional),
            "raw": dict (optional)
        }
        """
        pass
