"""The language of the current request: Slovak (default) or English.

The web app sends `Accept-Language: sk` or `en` with every request (the user
picks it with the SK | EN switch). language_middleware stores it in a
context variable for the duration of the request, so code anywhere below the
endpoint can produce a user-facing message with tr("…", "…") or ask
current_lang() — for example to tell the AI which language to answer in —
without threading a parameter through every call.

Anything that is not "en…" is Slovak: the mobile app and older clients send
nothing or "sk", and Slovak is what they have always received.
"""
from contextvars import ContextVar

_lang: ContextVar[str] = ContextVar("medicalai_lang", default="sk")


def lang_from_header(value: str | None) -> str:
    first = (value or "").split(",")[0].strip().lower()
    return "en" if first.startswith("en") else "sk"


def current_lang() -> str:
    return _lang.get()


def tr(sk: str, en: str) -> str:
    """The message in the language of the current request."""
    return en if _lang.get() == "en" else sk


def answer_language() -> str:
    """For AI prompts: the language the reply must be written in."""
    return "English" if _lang.get() == "en" else "Slovak (slovenčina)"


async def language_middleware(request, call_next):
    token = _lang.set(lang_from_header(request.headers.get("accept-language")))
    try:
        return await call_next(request)
    finally:
        _lang.reset(token)
