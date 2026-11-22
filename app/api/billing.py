"""Stripe subscriptions: plans, checkout, customer portal and the webhook.

Flow: the app POSTs /checkout with a plan → gets a Stripe Checkout URL →
the user pays on stripe.com → Stripe calls /webhook → the `subscriptions`
row is updated → GET /status says "pro". Cancelling, card changes and
invoices live in the Stripe customer portal (POST /portal).

The webhook URL must go through the Cloudflare Worker
(https://app.prognesa.ai/api/billing/webhook) so it carries X-Proxy-Secret.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth.demo import is_demo_email
from app.auth.dependencies import get_current_user
from app.billing import service, stripe_api
from app.config import settings
from app.database import User
from app.i18n import tr

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/billing", tags=["billing"])


def _require_billing() -> None:
    if not service.billing_enabled():
        raise HTTPException(status_code=503, detail=tr("Platby zatiaľ nie sú zapnuté.", "Payments are not enabled yet."))


@router.get("/plans")
def plans():
    """Which plans can be bought right now (prices themselves live in Stripe)."""
    return {"enabled": service.billing_enabled(), "plans": service.available_plans() if service.billing_enabled() else []}


@router.get("/status")
def status(current_user: User = Depends(get_current_user)):
    return service.status_for(current_user.id)


class CheckoutRequest(BaseModel):
    plan: str


@router.post("/checkout")
def checkout(data: CheckoutRequest, current_user: User = Depends(get_current_user)):
    _require_billing()
    if is_demo_email(current_user.email):
        raise HTTPException(status_code=403, detail=tr("V ukážkovom účte sa nedá platiť.", "The demo account cannot subscribe."))
    plan = next((p for p in service.available_plans() if p["id"] == data.plan), None)
    if plan is None:
        raise HTTPException(status_code=400, detail=tr("Neznámy plán.", "Unknown plan."))
    current = service.status_for(current_user.id)
    if current["tier"] != "free":
        raise HTTPException(
            status_code=409,
            detail=tr("Predplatné už máte — zmeniť ho môžete v správe predplatného.",
                      "You already have a subscription — change it in subscription management."),
        )

    base = settings.APP_BASE_URL.rstrip("/")
    try:
        customer_id = service.ensure_customer(current_user)
        session = stripe_api.create_checkout_session(
            customer_id=customer_id,
            price_id=service.price_id(data.plan),
            user_id=current_user.id,
            plan=data.plan,
            success_url=f"{base}/account?billing=success",
            cancel_url=f"{base}/account?billing=cancelled",
            # One trial per customer: no trial if they have subscribed before.
            trial_days=plan["trial_days"] if current["status"] is None else 0,
            automatic_tax=settings.STRIPE_AUTOMATIC_TAX,
        )
    except stripe_api.StripeError as e:
        logger.error("billing: checkout for user %s failed: %s", current_user.id, e)
        raise HTTPException(status_code=502, detail=tr("Platobnú bránu sa nepodarilo otvoriť. Skúste to znova.", "Could not open checkout. Please try again.")) from e
    return {"url": session["url"]}


@router.post("/portal")
def portal(current_user: User = Depends(get_current_user)):
    _require_billing()
    current = service.status_for(current_user.id)
    if not current["has_customer"]:
        raise HTTPException(status_code=404, detail=tr("Zatiaľ nemáte žiadne predplatné.", "You have no subscription yet."))
    from app.database import get_session
    session = get_session()
    try:
        customer_id = service.get_subscription(session, current_user.id).stripe_customer_id
    finally:
        session.close()
    try:
        portal_session = stripe_api.create_portal_session(customer_id, f"{settings.APP_BASE_URL.rstrip('/')}/account")
    except stripe_api.StripeError as e:
        logger.error("billing: portal for user %s failed: %s", current_user.id, e)
        raise HTTPException(status_code=502, detail=tr("Správu predplatného sa nepodarilo otvoriť.", "Could not open subscription management.")) from e
    return {"url": portal_session["url"]}


@router.post("/webhook")
async def webhook(request: Request):
    payload = await request.body()
    try:
        event = stripe_api.verify_webhook(payload, request.headers.get("stripe-signature", ""), settings.STRIPE_WEBHOOK_SECRET)
    except stripe_api.StripeError as e:
        logger.warning("billing: rejected webhook: %s", e)
        raise HTTPException(status_code=400, detail="Invalid signature")
    try:
        result = service.handle_event(event)
    except stripe_api.StripeError as e:
        # 500 → Stripe retries the event later.
        logger.error("billing: webhook %s (%s) failed: %s", event.get("id"), event.get("type"), e)
        raise HTTPException(status_code=500, detail="Retry later")
    return {"received": True, "result": result}
