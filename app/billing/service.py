"""Plans, subscription state and the webhook → database sync.

Stripe is the source of truth. The `subscriptions` row is a cache of what the
last webhook said, so "is this user on Pro?" is one indexed query.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.billing import stripe_api
from app.config import settings
from app.database import Subscription, User, get_session

logger = logging.getLogger(__name__)

# Statuses that unlock the paid plan. past_due keeps access while Stripe
# retries the card (Smart Retries), so a failed renewal is not an instant
# downgrade; Stripe moves it to canceled/unpaid if retries run out.
ENTITLED_STATUSES = {"trialing", "active", "past_due"}

# plan id → (settings attribute with the Stripe price id, interval, trial?)
PLANS: Dict[str, Dict[str, Any]] = {
    "pro_monthly": {"setting": "STRIPE_PRICE_PRO_MONTHLY", "tier": "pro", "interval": "month", "trial": True},
    "pro_yearly": {"setting": "STRIPE_PRICE_PRO_YEARLY", "tier": "pro", "interval": "year", "trial": True},
    "family_yearly": {"setting": "STRIPE_PRICE_FAMILY_YEARLY", "tier": "family", "interval": "year", "trial": True},
    "founding_yearly": {"setting": "STRIPE_PRICE_FOUNDING_YEARLY", "tier": "pro", "interval": "year", "trial": False},
}


def billing_enabled() -> bool:
    return bool(settings.STRIPE_SECRET_KEY)


def price_id(plan: str) -> str:
    meta = PLANS.get(plan)
    return getattr(settings, meta["setting"], "") if meta else ""


def available_plans() -> List[Dict[str, Any]]:
    out = []
    for plan, meta in PLANS.items():
        if not price_id(plan):
            continue
        if plan == "founding_yearly" and not settings.BILLING_FOUNDING_OPEN:
            continue
        out.append({
            "id": plan,
            "tier": meta["tier"],
            "interval": meta["interval"],
            "trial_days": settings.BILLING_TRIAL_DAYS if meta["trial"] else 0,
        })
    return out


def plan_for_price(price: Optional[str]) -> Optional[str]:
    if not price:
        return None
    for plan in PLANS:
        if price_id(plan) == price:
            return plan
    return None


def _ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def get_subscription(session, user_id: int) -> Optional[Subscription]:
    return session.query(Subscription).filter_by(user_id=user_id).first()


def status_for(user_id: int) -> Dict[str, Any]:
    session = get_session()
    try:
        sub = get_subscription(session, user_id)
        return serialize(sub)
    finally:
        session.close()


def serialize(sub: Optional[Subscription]) -> Dict[str, Any]:
    entitled = bool(sub and sub.status in ENTITLED_STATUSES)
    tier = PLANS.get(sub.plan, {}).get("tier") if (sub and entitled) else None
    return {
        "tier": tier or "free",
        "plan": sub.plan if entitled else None,
        "status": sub.status if sub else None,
        "current_period_end": sub.current_period_end.isoformat() if (sub and sub.current_period_end) else None,
        "cancel_at_period_end": bool(sub and sub.cancel_at_period_end),
        "has_customer": bool(sub and sub.stripe_customer_id),
    }


def ensure_customer(user: User) -> str:
    """The user's Stripe customer id, creating the customer on first use."""
    session = get_session()
    try:
        sub = get_subscription(session, user.id)
        if sub and sub.stripe_customer_id:
            return sub.stripe_customer_id
        customer = stripe_api.create_customer(user.email, user.id)
        if sub is None:
            sub = Subscription(user_id=user.id)
            session.add(sub)
        sub.stripe_customer_id = customer["id"]
        session.commit()
        return customer["id"]
    finally:
        session.close()


def apply_subscription(obj: Dict[str, Any]) -> Optional[int]:
    """Store a Stripe subscription object. Returns the user id it belonged to."""
    sub_id = obj.get("id")
    customer_id = obj.get("customer")
    metadata = obj.get("metadata") or {}
    items = ((obj.get("items") or {}).get("data") or [])
    price = ((items[0].get("price") or {}).get("id")) if items else None
    plan = plan_for_price(price) or metadata.get("plan")
    status = obj.get("status")

    session = get_session()
    try:
        row = None
        if sub_id:
            row = session.query(Subscription).filter_by(stripe_subscription_id=sub_id).first()
        if row is None and customer_id:
            row = session.query(Subscription).filter_by(stripe_customer_id=customer_id).first()
        if row is None and str(metadata.get("user_id", "")).isdigit():
            user_id = int(metadata["user_id"])
            if session.query(User).filter_by(id=user_id).first() is None:
                logger.warning("billing: subscription %s for unknown user %s", sub_id, user_id)
                return None
            row = get_subscription(session, user_id) or Subscription(user_id=user_id)
            session.add(row)
        if row is None:
            logger.warning("billing: subscription %s (customer %s) matches no user", sub_id, customer_id)
            return None

        # An event about an old, ended subscription must not overwrite a newer
        # live one (e.g. the user cancelled, then subscribed again).
        if (row.stripe_subscription_id and row.stripe_subscription_id != sub_id
                and row.status in ENTITLED_STATUSES and status not in ENTITLED_STATUSES):
            logger.info("billing: ignoring %s for old subscription %s", status, sub_id)
            return row.user_id

        row.stripe_subscription_id = sub_id
        if customer_id:
            row.stripe_customer_id = customer_id
        row.plan = plan
        row.status = status
        row.current_period_end = _ts(obj.get("current_period_end"))
        row.cancel_at_period_end = bool(obj.get("cancel_at_period_end"))
        session.commit()
        logger.info("billing: user %s → %s (%s)", row.user_id, plan, status)
        return row.user_id
    finally:
        session.close()


HANDLED_EVENTS = {
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "customer.subscription.paused",
    "customer.subscription.resumed",
}


def handle_event(event: Dict[str, Any]) -> str:
    kind = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}
    if kind not in HANDLED_EVENTS:
        return "ignored"
    if kind == "checkout.session.completed":
        sub_id = obj.get("subscription")
        if not sub_id:
            return "ignored"
        # Fetch the subscription itself: the checkout session does not carry
        # its status, price or period end.
        sub = stripe_api.retrieve_subscription(sub_id)
        sub.setdefault("metadata", {})
        if obj.get("client_reference_id") and "user_id" not in sub["metadata"]:
            sub["metadata"]["user_id"] = obj["client_reference_id"]
        apply_subscription(sub)
        return "ok"
    apply_subscription(obj)
    return "ok"


def cancel_for_deleted_account(user_id: int) -> None:
    """Best effort: stop billing a user whose account is being deleted."""
    if not billing_enabled():
        return
    session = get_session()
    try:
        sub = get_subscription(session, user_id)
        sub_id = sub.stripe_subscription_id if sub else None
        status = sub.status if sub else None
    finally:
        session.close()
    if sub_id and status in ENTITLED_STATUSES:
        try:
            stripe_api.cancel_subscription(sub_id)
            logger.info("billing: cancelled subscription %s of deleted user %s", sub_id, user_id)
        except stripe_api.StripeError as e:
            logger.error("billing: could not cancel subscription %s of deleted user %s: %s", sub_id, user_id, e)
