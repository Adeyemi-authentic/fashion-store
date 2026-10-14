"""In-memory stand-in for Paystack, used by tests (and nothing else)."""

from typing import Any

from app.modules.payments.paystack import Charge, PaystackError


class FakePaystack:
    def __init__(self) -> None:
        self.initialized: dict[str, dict[str, Any]] = {}  # reference -> request
        self.charges: dict[str, Charge] = {}  # what verify() returns
        self.refunds: list[tuple[int, int]] = []
        self.fail_initialize = False

    async def initialize(
        self,
        *,
        email: str,
        amount_kobo: int,
        reference: str,
        callback_url: str,
        metadata: dict[str, Any],
    ) -> str:
        if self.fail_initialize:
            raise PaystackError("Paystack unreachable: ConnectTimeout")
        if reference in self.initialized:
            raise PaystackError("Duplicate Transaction Reference")
        self.initialized[reference] = {"email": email, "amount": amount_kobo, "metadata": metadata}
        return f"https://checkout.paystack.test/{reference}"

    async def verify(self, reference: str) -> Charge:
        return self.charges.get(reference, Charge(reference, "abandoned", 0, "NGN", None))

    async def refund(self, *, transaction_id: int, amount_kobo: int) -> None:
        self.refunds.append((transaction_id, amount_kobo))

    async def list_refunds(self, transaction_id: int) -> list[str]:
        return ["pending" for t, _ in self.refunds if t == transaction_id]

    def pay(self, reference: str, amount_kobo: int | None = None, currency: str = "NGN") -> Charge:
        """Simulate the customer paying on Paystack's page."""
        amount = amount_kobo if amount_kobo is not None else self.initialized[reference]["amount"]
        charge = Charge(reference, "success", amount, currency, 4_000_000 + len(self.charges))
        self.charges[reference] = charge
        return charge
