"""Subscriptions with Stripe: Start, Pro and Studio (app/plans.py), monthly or yearly.

Stripe hosts the payment page (Checkout), sends receipts and invoices, and runs the
page where subscribers change card, download invoices or cancel (the customer portal).

Nothing has to be set up by hand in Stripe: the products ("remixfox_start", ...) and
their prices (lookup keys "remixfox_start_month", ...) are created from app/plans.py
the first time they are needed, and a price is replaced when the amount in
app/plans.py changes. Set STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET (for
/api/stripe/webhook); until then the pricing says payments open soon.
"""

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from sqlmodel import Session, select

from app import plans
from app.auth import CurrentUser
from app.config import settings
from app.db import engine
from app.models import ACTIVE_SUBSCRIPTIONS, CheckoutIn, User, utcnow

STRIPE_API = "https://api.stripe.com/v1"
TERMS_VERSION = "2026-11-11"  # the "Last updated" date of public/terms.html, kept with each new subscription
SIGNATURE_TOLERANCE_SECONDS = 300

router = APIRouter(prefix="/api")
_prices: dict[str, str] = {}  # lookup key -> Stripe price id, once checked
_payments: dict = {}  # the admin panel's payment figures, kept for PAYMENTS_CACHE_SECONDS
PAYMENTS_CACHE_SECONDS = 300


def is_configured() -> bool:
    return bool(settings.stripe_secret_key)


def stripe(method: str, path: str, data: dict | None = None, missing_ok: bool = False) -> dict | None:
    try:
        response = httpx.request(
            method, f"{STRIPE_API}{path}", data=data, auth=(settings.stripe_secret_key, ""), timeout=20,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Could not reach Stripe ({type(exc).__name__})") from exc
    body = response.json() if response.content else {}
    if response.status_code == 404 and missing_ok:
        return None
    if response.status_code >= 400:
        message = (body.get("error") or {}).get("message", response.reason_phrase)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Stripe: {message}")
    return body


def payments() -> dict:
    """Paid invoices, from Stripe, for the admin panel: how many and how much, in pounds."""
    if not is_configured():
        return {"configured": False, "error": None}
    if _payments and time.monotonic() - _payments["at"] < PAYMENTS_CACHE_SECONDS:
        return _payments["data"]
    month = plans.month_start().timestamp()
    count = month_count = total = month_total = 0
    after = ""
    try:
        for _ in range(20):  # up to 2,000 invoices: plenty for a small product
            page = stripe("GET", "/invoices?status=paid&limit=100" + (f"&starting_after={after}" if after else ""))
            for invoice in page.get("data", []):
                paid = invoice.get("amount_paid") or 0
                if paid > 0:
                    count, total = count + 1, total + paid
                    if (invoice.get("created") or 0) >= month:
                        month_count, month_total = month_count + 1, month_total + paid
            if not page.get("has_more") or not page.get("data"):
                break
            after = page["data"][-1]["id"]
    except HTTPException as exc:
        return {"configured": True, "error": exc.detail}
    data = {"configured": True, "error": None, "payments": count, "revenue": total / 100,
            "payments_this_month": month_count, "revenue_this_month": month_total / 100}
    _payments.update(at=time.monotonic(), data=data)
    return data


def _require_billing() -> None:
    if not is_configured():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Payments are not set up yet")


def _base_url(request: Request) -> str:
    return settings.public_url or str(request.base_url).rstrip("/")


# ---------- Products and prices, created from app/plans.py ----------

def lookup_key(plan: str, interval: str) -> str:
    return f"remixfox_{plan}_{interval}"


def price_id(plan_key: str, interval: str) -> str:
    """The Stripe price for this plan and interval, created (or replaced) when needed."""
    key = lookup_key(plan_key, interval)
    if key in _prices:
        return _prices[key]
    plan = plans.PLANS[plan_key]
    amount = plan.pence(interval)
    found = stripe("GET", f"/prices?lookup_keys[]={key}&active=true&limit=1")["data"]
    if found and found[0]["unit_amount"] == amount and found[0]["currency"] == "gbp":
        _prices[key] = found[0]["id"]
        return _prices[key]
    product = f"remixfox_{plan_key}"
    if stripe("GET", f"/products/{product}", missing_ok=True) is None:
        stripe("POST", "/products", {"id": product, "name": f"Remixfox {plan.name}", "description": plan.tagline})
    price = stripe("POST", "/prices", {
        "product": product, "currency": "gbp", "unit_amount": str(amount),
        "recurring[interval]": interval, "lookup_key": key, "transfer_lookup_key": "true",
        "nickname": f"{plan.name}, {'monthly' if interval == 'month' else 'yearly'}",
    })
    _prices[key] = price["id"]
    return _prices[key]


def plan_of_price(price: dict) -> tuple[str | None, str | None]:
    """(plan, interval) from a Stripe price, by its lookup key, else its product."""
    parts = (price.get("lookup_key") or "").split("_")
    if len(parts) == 3 and parts[1] in plans.PLANS and parts[2] in plans.INTERVALS:
        return parts[1], parts[2]
    product = price.get("product")
    product_id = product.get("id") if isinstance(product, dict) else product
    plan = (product_id or "").removeprefix("remixfox_")
    interval = (price.get("recurring") or {}).get("interval")
    return (plan if plan in plans.PLANS else None), (interval if interval in plans.INTERVALS else None)


# ---------- Endpoints ----------

@router.get("/billing/plans")
def list_plans() -> dict:
    """The plans on offer (public: the landing page and Settings show them)."""
    return {
        "configured": is_configured(), "currency": "GBP", "annual_discount": plans.ANNUAL_DISCOUNT,
        "plans": [plan.public() for plan in plans.PLANS.values()],
    }


@router.post("/billing/checkout")
def checkout(payload: CheckoutIn, request: Request, user: CurrentUser) -> dict:
    """Subscribe (Stripe's payment page), or switch an active subscription to this plan."""
    _require_billing()
    price = price_id(payload.plan, payload.interval)
    if user.stripe_subscription_id and user.subscription_status in ACTIVE_SUBSCRIPTIONS:
        subscription = stripe("GET", f"/subscriptions/{user.stripe_subscription_id}")
        item = subscription["items"]["data"][0]
        if item["price"]["id"] == price:
            raise HTTPException(status.HTTP_409_CONFLICT, "You are already on this plan")
        updated = stripe("POST", f"/subscriptions/{user.stripe_subscription_id}", {
            "items[0][id]": item["id"], "items[0][price]": price, "proration_behavior": "create_prorations",
        })
        with Session(engine) as session:
            fresh = session.get(User, user.id)
            apply_subscription(fresh, updated)
            session.commit()
        return {"changed": True}
    if user.stripe_subscription_id and user.subscription_status in ("past_due", "unpaid", "incomplete"):
        # Paying again on a new page would start a second subscription next to the unpaid one.
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Your last payment did not go through. Update your card in Manage billing first.")
    if not payload.consent:
        # UK consumer law: the service may start inside the 14-day cancellation period only at the
        # customer's express request, which the box on the Plans page records.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "Tick the box to agree to the Terms of use and start your plan straight away")
    base = _base_url(request)
    accepted = {"terms_version": TERMS_VERSION, "terms_accepted_at": utcnow().isoformat(timespec="seconds")}
    data = {
        "mode": "subscription",
        "line_items[0][price]": price,
        "line_items[0][quantity]": "1",
        "client_reference_id": str(user.id),
        "allow_promotion_codes": "true",
        "billing_address_collection": "required",  # where customers are: needed for VAT records
        "custom_text[submit][message]": (
            "By subscribing you agree to the Remixfox Terms of use (remixfox.co.uk/terms.html) and ask us to "
            "start your plan now. You can cancel at any time on the Plans page."
        ),
        "success_url": f"{base}/?billing=success",
        "cancel_url": f"{base}/?billing=cancelled",
        **{f"metadata[{key}]": value for key, value in accepted.items()},
        **{f"subscription_data[metadata][{key}]": value for key, value in accepted.items()},
    }
    if user.stripe_customer_id:
        data["customer"] = user.stripe_customer_id
    elif user.email:  # Telegram-only accounts type their e-mail on Stripe's page
        data["customer_email"] = user.email
    return {"url": stripe("POST", "/checkout/sessions", data)["url"]}


@router.post("/billing/portal")
def portal(request: Request, user: CurrentUser) -> dict:
    """Stripe's page for changing the card, downloading invoices or cancelling."""
    _require_billing()
    if not user.stripe_customer_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "You have no subscription yet")
    session = stripe("POST", "/billing_portal/sessions", {
        "customer": user.stripe_customer_id, "return_url": f"{_base_url(request)}/?view=account",
    })
    return {"url": session["url"]}


# ---------- Webhook: Stripe tells us when a subscription changes ----------

def verify_signature(payload: bytes, header: str, secret: str, now: float | None = None) -> bool:
    """Stripe's scheme: HMAC-SHA256 of "{timestamp}.{payload}", within 5 minutes."""
    parts = [item.split("=", 1) for item in header.split(",") if "=" in item]
    timestamps = [value for key, value in parts if key == "t"]
    signatures = [value for key, value in parts if key == "v1"]
    if not timestamps or not signatures or not secret:
        return False
    try:
        timestamp = int(timestamps[0])
    except ValueError:
        return False
    if abs((now or time.time()) - timestamp) > SIGNATURE_TOLERANCE_SECONDS:
        return False
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, signature) for signature in signatures)


def apply_subscription(user: User, subscription: dict) -> None:
    user.stripe_subscription_id = subscription.get("id") or user.stripe_subscription_id
    user.subscription_status = subscription.get("status")
    # Newer Stripe API versions keep the period and price on the subscription's items.
    items = (subscription.get("items") or {}).get("data") or [{}]
    period_end = subscription.get("current_period_end") or items[0].get("current_period_end")
    user.subscription_renews_at = datetime.fromtimestamp(period_end, timezone.utc) if period_end else None
    plan, interval = plan_of_price(items[0].get("price") or {})
    if plan:
        user.plan, user.plan_interval = plan, interval


@router.post("/stripe/webhook")
async def webhook(request: Request) -> dict:
    payload = await request.body()
    if not verify_signature(payload, request.headers.get("stripe-signature", ""), settings.stripe_webhook_secret):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid signature")
    event = json.loads(payload)
    kind, obj = event.get("type", ""), (event.get("data") or {}).get("object") or {}
    with Session(engine) as session:
        if kind == "checkout.session.completed" and obj.get("client_reference_id"):
            user = session.get(User, int(obj["client_reference_id"]))
            if user:
                user.stripe_customer_id = obj.get("customer") or user.stripe_customer_id
                if obj.get("subscription"):
                    apply_subscription(user, stripe("GET", f"/subscriptions/{obj['subscription']}"))
        elif kind.startswith("customer.subscription."):
            user = session.exec(select(User).where(User.stripe_customer_id == obj.get("customer"))).first()
            if user:
                apply_subscription(user, obj)
        session.commit()
    return {"received": True}
