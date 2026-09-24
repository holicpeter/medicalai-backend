"""Free daily AI allowance per user.

Registration is open and every chat answer, OCR'd document, meal analysis and
Claude risk analysis is paid for by this app's own Anthropic key, so without
a cap one account (or a script behind one) could spend the whole balance.
Each AI-backed endpoint takes one unit of its kind before calling the model
and gives it back if the call fails, so an outage does not eat the
allowance. Admins (settings.ADMIN_EMAILS) are not limited.

The count lives in the ai_usage table, not in memory, so a redeploy does not
reset it. The day turns over at midnight Europe/Bratislava — the users are
in Slovakia and "resets at midnight" should mean their midnight.
"""
import logging
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from typing import Dict, Iterator, List
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.database import AiUsage, User, get_session

logger = logging.getLogger(__name__)

_TZ = ZoneInfo("Europe/Bratislava")

# Header on the 429 so a client can tell "free allowance used up" apart from
# the login rate limiter's 429 and show the account message instead.
QUOTA_HEADER = "X-AI-Quota-Exceeded"

# kind -> (settings attribute holding the limit, label on the account screen,
#          phrase for the "used up" message)
KINDS: Dict[str, tuple] = {
    "chat": ("AI_DAILY_LIMIT_CHAT", "Správy v AI chate", "na správy v AI chate"),
    "documents": (
        "AI_DAILY_LIMIT_DOCUMENTS",
        "Nahraté dokumenty (čítanie cez AI)",
        "na čítanie dokumentov cez AI",
    ),
    "nutrition": ("AI_DAILY_LIMIT_NUTRITION", "Analýzy jedla", "na analýzy jedla"),
    "risk_analysis": ("AI_DAILY_LIMIT_RISK_ANALYSIS", "AI analýzy rizík", "na AI analýzy rizík"),
}


def _limit(kind: str) -> int:
    return int(getattr(settings, KINDS[kind][0]))


def today() -> date:
    return datetime.now(_TZ).date()


def resets_at() -> datetime:
    return datetime.combine(today() + timedelta(days=1), time.min, tzinfo=_TZ)


def is_unlimited(user: User) -> bool:
    return user.email.lower() in {e.lower() for e in settings.ADMIN_EMAILS}


def exceeded_message(kind: str) -> str:
    return (
        f"Minuli ste dnešné bezplatné kredity {KINDS[kind][2]} "
        f"({_limit(kind)} denne). Obnovia sa o polnoci."
    )


def _take(user_id: int, kind: str, limit: int) -> bool:
    """Atomically add one unit if the user is still under the limit."""
    day = today()
    session = get_session()
    try:
        # Conditional UPDATE: two concurrent requests at count == limit - 1
        # cannot both get through, whatever the isolation level.
        updated = (
            session.query(AiUsage)
            .filter(
                AiUsage.user_id == user_id,
                AiUsage.day == day,
                AiUsage.kind == kind,
                AiUsage.count < limit,
            )
            .update({AiUsage.count: AiUsage.count + 1}, synchronize_session=False)
        )
        if updated:
            session.commit()
            return True

        exists = session.query(AiUsage.id).filter_by(user_id=user_id, day=day, kind=kind).first()
        if exists is not None:
            return False  # a row for today exists and is at the limit
        if limit <= 0:
            return False

        session.add(AiUsage(user_id=user_id, day=day, kind=kind, count=1))
        try:
            session.commit()
            return True
        except IntegrityError:
            # Another request created today's row first; retry as an update.
            session.rollback()
            return _take(user_id, kind, limit)
    finally:
        session.close()


def _give_back(user_id: int, kind: str) -> None:
    session = get_session()
    try:
        (
            session.query(AiUsage)
            .filter(
                AiUsage.user_id == user_id,
                AiUsage.day == today(),
                AiUsage.kind == kind,
                AiUsage.count > 0,
            )
            .update({AiUsage.count: AiUsage.count - 1}, synchronize_session=False)
        )
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("quota: could not refund %s for user %s", kind, user_id)
    finally:
        session.close()


def consume(user: User, kind: str, units: int = 1) -> int:
    """Take `units` of `kind` or raise 429. Returns how many were taken (0 for admins)."""
    if is_unlimited(user) or units <= 0:
        return 0
    limit = _limit(kind)
    taken = 0
    for _ in range(units):
        if not _take(user.id, kind, limit):
            for _ in range(taken):
                _give_back(user.id, kind)
            logger.info("quota: user %s hit the daily %s limit (%d)", user.id, kind, limit)
            raise HTTPException(
                status_code=429,
                detail=exceeded_message(kind),
                headers={QUOTA_HEADER: kind},
            )
        taken += 1
    return taken


def refund(user: User, kind: str, units: int = 1) -> None:
    for _ in range(units):
        _give_back(user.id, kind)


@contextmanager
def ai_call(user: User, kind: str, units: int = 1) -> Iterator[None]:
    """Hold `units` of the allowance for the duration of an AI call.

    Refunded if the block raises, so a model outage or a rejected file does
    not count against the user.
    """
    taken = consume(user, kind, units)
    try:
        yield
    except BaseException:
        refund(user, kind, taken)
        raise


def usage_summary(user: User) -> List[dict]:
    """Today's usage per kind, for the account screen."""
    unlimited = is_unlimited(user)
    session = get_session()
    try:
        rows = session.query(AiUsage).filter_by(user_id=user.id, day=today()).all()
        used = {row.kind: row.count for row in rows}
    finally:
        session.close()

    out = []
    for kind, (_, label, _phrase) in KINDS.items():
        limit = _limit(kind)
        count = used.get(kind, 0)
        out.append({
            "kind": kind,
            "label": label,
            "used": count,
            "limit": None if unlimited else limit,
            "remaining": None if unlimited else max(limit - count, 0),
            "exhausted": False if unlimited else count >= limit,
        })
    return out
