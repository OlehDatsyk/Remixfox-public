"""Subscriptions with Stripe: Start, Pro and Studio, monthly or yearly (Stripe faked)."""

import hashlib
import hmac
import json
import time
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from app import auth, billing
from tests.conftest import sign_up, user_id

SECRET = "whsec_test"


@pytest.fixture
def stripe_ready(monkeypatch):
    """Stripe keys set, and a small fake Stripe: .calls records requests, .prices the prices it has."""
    configured = replace(billing.settings, stripe_secret_key="sk_test_x", stripe_webhook_secret=SECRET)
    monkeypatch.setattr(billing, "settings", configured)
    monkeypatch.setattr(auth, "settings", configured)
    billing._prices.clear()

    class Fake:
        calls: list[tuple[str, str, dict | None]] = []
        prices: dict[str, dict] = {}  # lookup key -> price
        products: set[str] = set()
        subscription: dict = {}

    fake = Fake()
    fake.calls, fake.prices, fake.products, fake.subscription = [], {}, set(), {}

    def stripe(method, path, data=None, missing_ok=False):
        fake.calls.append((method, path, data))
        if method == "GET" and path.startswith("/prices?lookup_keys[]="):
            key = path.split("=")[1].split("&")[0]
            return {"data": [fake.prices[key]] if key in fake.prices else []}
        if method == "GET" and path.startswith("/products/"):
            return {"id": path.split("/")[2]} if path.split("/")[2] in fake.products else None
        if method == "POST" and path == "/products":
            fake.products.add(data["id"])
            return {"id": data["id"]}
        if method == "POST" and path == "/prices":
            price = {"id": f"price_{data['lookup_key']}_{len(fake.calls)}", "lookup_key": data["lookup_key"],
                     "unit_amount": int(data["unit_amount"]), "currency": data["currency"],
                     "product": data["product"], "recurring": {"interval": data["recurring[interval]"]}}
            fake.prices[data["lookup_key"]] = price
            return price
        if method == "POST" and path == "/checkout/sessions":
            return {"url": "https://checkout.stripe.com/c/pay/cs_test_1"}
        if method == "GET" and path.startswith("/subscriptions/"):
            return fake.subscription
        if method == "POST" and path.startswith("/subscriptions/"):
            new_price = next(p for p in fake.prices.values() if p["id"] == data["items[0][price]"])
            fake.subscription["items"]["data"][0]["price"] = new_price
            return fake.subscription
        if method == "POST" and path == "/billing_portal/sessions":
            return {"url": "https://billing.stripe.com/p/session/x"}
        raise AssertionError((method, path))

    monkeypatch.setattr(billing, "stripe", stripe)
    yield fake
    billing._prices.clear()


def signed(event: dict, secret: str = SECRET, at: float | None = None) -> tuple[bytes, dict]:
    payload = json.dumps(event).encode()
    timestamp = int(at or time.time())
    signature = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
    return payload, {"stripe-signature": f"t={timestamp},v1={signature}", "content-type": "application/json"}


def subscription(price: dict, status: str = "active") -> dict:
    return {"id": "sub_1", "status": status, "customer": "cus_1",
            "items": {"data": [{"id": "si_1", "current_period_end": 1_800_000_000, "price": price}]}}


def subscribe_through_stripe(client, stripe_ready, plan="pro", interval="month") -> None:
    """Checkout, then Stripe's webhook, as when someone really pays."""
    client.post("/api/billing/checkout", json={"plan": plan, "interval": interval, "consent": True})
    stripe_ready.subscription = subscription(stripe_ready.prices[f"remixfox_{plan}_{interval}"])
    paid = {"type": "checkout.session.completed", "data": {"object": {
        "client_reference_id": str(user_id("someone@example.com")), "customer": "cus_1", "subscription": "sub_1",
    }}}
    payload, headers = signed(paid)
    assert client.post("/api/stripe/webhook", content=payload, headers=headers).status_code == 200


def test_the_plans_are_public_in_pounds_with_a_yearly_discount(client):
    client.cookies.clear()
    offer = client.get("/api/billing/plans").json()
    assert (offer["configured"], offer["currency"], offer["annual_discount"]) == (False, "GBP", 0.2)
    plans = {p["key"]: p for p in offer["plans"]}
    assert [p["name"] for p in offer["plans"]] == ["Start", "Pro", "Studio"]
    assert [(p["monthly"], p["yearly"]) for p in offer["plans"]] == [(15, 144), (39, 374), (99, 950)]
    assert plans["pro"]["yearly_per_month"] == 31.17
    assert [(p["competitors"], p["videos"]) for p in offer["plans"]] == [(3, 60), (8, 250), (20, 800)]
    assert [(p["exports"], p["notion"]) for p in offer["plans"]] == [(False, False), (True, False), (True, True)]


def test_without_stripe_keys_payments_are_not_set_up(client):
    sign_up(client, "someone@example.com")
    assert client.post("/api/billing/checkout", json={"plan": "start", "interval": "month"}).status_code == 503
    assert client.post("/api/billing/portal").status_code == 503
    me = client.get("/api/me").json()
    assert (me["billing_ready"], me["allowance"]["label"]) == (False, "Free trial")


def test_choosing_a_plan_creates_it_in_stripe_once(client, stripe_ready):
    sign_up(client, "someone@example.com")
    response = client.post("/api/billing/checkout", json={"plan": "pro", "interval": "year", "consent": True})
    assert response.json() == {"url": "https://checkout.stripe.com/c/pay/cs_test_1"}

    created = [data for method, path, data in stripe_ready.calls if (method, path) == ("POST", "/prices")]
    assert created == [{
        "product": "remixfox_pro", "currency": "gbp", "unit_amount": "37400", "recurring[interval]": "year",
        "lookup_key": "remixfox_pro_year", "transfer_lookup_key": "true", "nickname": "Pro, yearly",
    }]
    assert stripe_ready.products == {"remixfox_pro"}
    checkout = next(data for method, path, data in stripe_ready.calls if path == "/checkout/sessions")
    assert checkout["line_items[0][price]"] == stripe_ready.prices["remixfox_pro_year"]["id"]
    assert checkout["customer_email"] == "someone@example.com"
    assert checkout["client_reference_id"] == str(user_id("someone@example.com"))
    assert checkout["success_url"] == "http://testserver/?billing=success"

    # The customer's request to start at once is recorded with Stripe, and their country is collected.
    assert checkout["metadata[terms_version]"] == checkout["subscription_data[metadata][terms_version]"] == billing.TERMS_VERSION
    assert checkout["metadata[terms_accepted_at]"].startswith(str(datetime.now(timezone.utc).year))
    assert checkout["billing_address_collection"] == "required"
    assert "Terms of use" in checkout["custom_text[submit][message]"]

    # Next time the price is found by its lookup key, not created again.
    billing._prices.clear()
    client.post("/api/billing/checkout", json={"plan": "pro", "interval": "year", "consent": True})
    assert len([c for c in stripe_ready.calls if c[:2] == ("POST", "/prices")]) == 1


def test_a_first_subscription_needs_the_terms_box_ticked(client, stripe_ready):
    sign_up(client, "someone@example.com")
    refused = client.post("/api/billing/checkout", json={"plan": "start", "interval": "month"})
    assert refused.status_code == 422 and "Terms of use" in refused.json()["detail"]
    assert not [c for c in stripe_ready.calls if c[1] == "/checkout/sessions"]
    terms = client.get("/terms.html").text
    assert "ticking the box on the Plans" in terms  # the Terms describe the box
    assert f"Last updated: {datetime.fromisoformat(billing.TERMS_VERSION).day} November 2026" in terms


def test_a_new_price_in_the_code_replaces_the_old_one_in_stripe(client, stripe_ready):
    stripe_ready.prices["remixfox_start_month"] = {"id": "price_old", "lookup_key": "remixfox_start_month",
                                                   "unit_amount": 1200, "currency": "gbp"}
    stripe_ready.products.add("remixfox_start")
    assert billing.price_id("start", "month") != "price_old"
    assert stripe_ready.prices["remixfox_start_month"]["unit_amount"] == 1500


def test_stripe_tells_us_the_plan_and_switching_changes_it(client, stripe_ready, fake_apify):
    sign_up(client, "someone@example.com")
    subscribe_through_stripe(client, stripe_ready, "pro", "month")
    me = client.get("/api/me").json()
    assert (me["plan"], me["plan_interval"], me["subscription_status"], me["can_fetch"]) == ("pro", "month", "active", True)
    assert (me["allowance"]["label"], me["allowance"]["competitors"], me["allowance"]["videos"]) == ("Pro", 8, 250)
    assert me["subscription_renews_at"].startswith("2027-01-15")

    # Already on Pro monthly; Studio switches the subscription in place (Stripe prorates).
    assert client.post("/api/billing/checkout", json={"plan": "pro", "interval": "month"}).status_code == 409
    assert client.post("/api/billing/checkout", json={"plan": "studio", "interval": "month"}).json() == {"changed": True}
    switch = next(data for method, path, data in stripe_ready.calls if (method, path) == ("POST", "/subscriptions/sub_1"))
    assert (switch["items[0][id]"], switch["proration_behavior"]) == ("si_1", "create_prorations")
    assert client.get("/api/me").json()["plan"] == "studio"

    assert client.post("/api/billing/portal").json()["url"] == "https://billing.stripe.com/p/session/x"

    cancelled = {"type": "customer.subscription.deleted", "data": {"object": subscription(
        stripe_ready.prices["remixfox_studio_month"], status="canceled")}}
    payload, headers = signed(cancelled)
    client.post("/api/stripe/webhook", content=payload, headers=headers)
    me = client.get("/api/me").json()
    assert (me["subscription_status"], me["allowance"]["label"]) == ("canceled", "Free trial")


def test_a_failed_payment_is_fixed_with_the_card_not_a_second_subscription(client, stripe_ready):
    sign_up(client, "someone@example.com")
    subscribe_through_stripe(client, stripe_ready, "start", "month")
    failed = {"type": "customer.subscription.updated", "data": {"object": subscription(
        stripe_ready.prices["remixfox_start_month"], status="past_due")}}
    payload, headers = signed(failed)
    client.post("/api/stripe/webhook", content=payload, headers=headers)

    refused = client.post("/api/billing/checkout", json={"plan": "pro", "interval": "month"})
    assert refused.status_code == 409 and "Update your card in Manage billing" in refused.json()["detail"]
    assert client.get("/api/me").json()["allowance"]["label"] == "Free trial"


def test_the_webhook_needs_stripes_signature(client, stripe_ready):
    event = {"type": "customer.subscription.updated", "data": {"object": {"customer": "cus_1", "status": "active"}}}
    for payload, headers in (signed(event, secret="whsec_wrong"), signed(event, at=time.time() - 600)):
        assert client.post("/api/stripe/webhook", content=payload, headers=headers).status_code == 400
    assert client.post("/api/stripe/webhook", content=b"{}").status_code == 400


@pytest.mark.parametrize("header", ["", "t=abc,v1=00", "v1=00", "t=1700000000"])
def test_malformed_signatures_are_refused(header):
    assert billing.verify_signature(b"{}", header, SECRET) is False
