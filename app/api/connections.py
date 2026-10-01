"""The "Prepojenia" (connections) page: which services a user has linked.

Every user connects their own accounts; nothing here is shared between
users. See app/integrations/connections.py for how a connection is made.
"""
from fastapi import APIRouter, Depends, HTTPException

from app.api.integrations import withings_authorize_url
from app.auth.dependencies import get_current_user
from app.database import User
from app.integrations import connections

router = APIRouter(prefix="/api/connections", tags=["connections"])

_DISCONNECTABLE = {"withings"}


@router.get("")
def list_connections(user: User = Depends(get_current_user)):
    return {"connections": connections.list_connections(user.id)}


@router.post("/withings/connect")
def connect_withings(user: User = Depends(get_current_user)):
    """The Withings consent page to send the browser to."""
    return {"authorize_url": withings_authorize_url(user)}


@router.post("/withings/sync")
async def sync_withings(user: User = Depends(get_current_user)):
    """Fetch the last 30 days from Withings now (the app also syncs on its own every few hours)."""
    from app.integrations.withings_connector import WithingsNotConnected, get_withings_connector

    if not get_withings_connector(user.id).is_authenticated:
        raise HTTPException(status_code=401, detail="Withings nie je pripojený.")
    try:
        return await connections.sync_withings(user.id, days=30)
    except WithingsNotConnected as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception:
        raise HTTPException(
            status_code=502,
            detail="Withings teraz neodpovedá. Skúste to o chvíľu znova.",
        )


@router.delete("/{provider}")
def disconnect(provider: str, user: User = Depends(get_current_user)):
    if provider not in _DISCONNECTABLE:
        raise HTTPException(status_code=404, detail="Neznáme prepojenie.")
    connections.disconnect(user.id, provider)
    return {"disconnected": provider}
