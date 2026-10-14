"""A local stand-in for Paystack's API, for load tests only (never used by the app itself).

The load test starts the real API with PAYSTACK_BASE_URL pointing here, so checkout runs its
normal HTTP code path without touching Paystack. Answers the three calls the app makes.

    uv run uvicorn scripts.load.fake_paystack:app --port 8790
"""

from typing import Any

from fastapi import FastAPI

app = FastAPI()
initialized: dict[str, dict[str, Any]] = {}


@app.post("/transaction/initialize")
async def initialize(body: dict[str, Any]) -> dict[str, Any]:
    ref = body["reference"]
    if ref in initialized:
        return {"status": False, "message": "Duplicate Transaction Reference"}
    initialized[ref] = body
    return {"status": True, "data": {"authorization_url": f"http://fake-paystack/{ref}"}}


@app.get("/transaction/verify/{reference}")
async def verify(reference: str) -> dict[str, Any]:
    body = initialized.get(reference, {})
    return {
        "status": True,
        "data": {
            "id": None,
            "reference": reference,
            "status": "abandoned",
            "amount": body.get("amount", 0),
            "currency": "NGN",
        },
    }


@app.get("/stats")
async def stats() -> dict[str, int]:
    return {"initialized": len(initialized)}
