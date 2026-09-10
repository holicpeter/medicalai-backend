"""
Withings connector - ScanWatch 2 + Body Scan

Štruktúra kopíruje garmin_connector.py: trieda + singleton cez
get_withings_connector(), async metódy, perzistencia do data/withings/.

Rozdiel oproti Garminu: Withings používa OAuth2, nie email+heslo. Tokeny
sa preto ukladajú na disk a obnovujú automaticky.

Setup:
    WITHINGS_CLIENT_ID=...
    WITHINGS_CLIENT_SECRET=...
    WITHINGS_REDIRECT_URI=http://localhost:3000/api/auth/callback/withings
"""
import asyncio
import json
import os
import statistics
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx
from sqlalchemy import create_engine, text

API_BASE = "https://wbsapi.withings.net"
ACCOUNT_BASE = "https://account.withings.com"

DATA_DIR = Path("data/withings")
# Súbor zostáva ako záloha pre lokálny beh bez databázy. Na Railway je
# filesystem efemérny - pri každom deployi sa zmaže a autorizácia padne.
TOKEN_FILE = DATA_DIR / "tokens.json"

DB_URL = os.environ.get("DATABASE_URL", "")

_TOKEN_DDL = """
CREATE TABLE IF NOT EXISTS withings_tokens (
    id               integer PRIMARY KEY,
    withings_user_id bigint,
    access_token     text NOT NULL,
    refresh_token    text NOT NULL,
    expires_at       double precision NOT NULL,
    updated_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT withings_tokens_single_row CHECK (id = 1)
)
"""

_engine = None


def _db():
    """
    Engine pre uloženie tokenov, alebo None keď DATABASE_URL nie je nastavená.

    Tabuľku vytvárame lenivo pri prvom použití - je to jedna tabuľka s jedným
    riadkom a takto netreba samostatnú migráciu.
    """
    global _engine
    if not DB_URL:
        return None
    if _engine is None:
        # Railway niekedy dáva starý prefix postgres://, ktorý SQLAlchemy 2.0
        # už neakceptuje.
        url = DB_URL.replace("postgres://", "postgresql://", 1)
        eng = create_engine(url, pool_pre_ping=True, future=True)
        with eng.begin() as conn:
            conn.execute(text(_TOKEN_DDL))
        _engine = eng
    return _engine

# meastype kódy. Hodnota v API = value * 10^unit
MEASTYPES = {
    1: ("weight", "kg"),
    4: ("height", "m"),
    5: ("fat_free_mass", "kg"),
    6: ("fat_ratio", "%"),
    8: ("fat_mass", "kg"),
    9: ("bp_diastolic", "mmHg"),
    10: ("bp_systolic", "mmHg"),
    11: ("heart_rate", "bpm"),
    12: ("temperature", "degC"),
    54: ("spo2", "%"),
    71: ("body_temperature", "degC"),
    73: ("skin_temperature", "degC"),
    76: ("muscle_mass", "kg"),
    77: ("hydration", "kg"),
    88: ("bone_mass", "kg"),
    91: ("pulse_wave_velocity", "m/s"),
    123: ("vo2max", "ml/kg/min"),
    135: ("qrs_interval", "ms"),
    136: ("pr_interval", "ms"),
    137: ("qt_interval", "ms"),
    138: ("qt_corrected", "ms"),
    139: ("atrial_fibrillation", "flag"),
}

# Čo produkuje ScanWatch 2 (bez váhy a bioimpedancie)
WATCH_MEASTYPES = [11, 12, 54, 71, 73, 91, 123, 135, 136, 137, 138, 139]



def _iso(epoch) -> str:
    """
    Epoch -> ISO 8601 s explicitným UTC offsetom.

    `datetime.fromtimestamp(x)` bez tz berie časovú zónu servera. Na Railway je
    to UTC a výsledok nemá žiadne označenie zóny, takže sa tvári ako lokálny
    čas. Noc, ktorá v Health Mate začala 23:47 SELČ, by sa potom zobrazila ako
    21:47. Frontend si offset prepočíta sám, ale musí ho dostať.
    """
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat()


class WithingsConnector:
    def __init__(self):
        self.client_id = os.environ.get("WITHINGS_CLIENT_ID", "")
        self.client_secret = os.environ.get("WITHINGS_CLIENT_SECRET", "")
        self.redirect_uri = os.environ.get("WITHINGS_REDIRECT_URI", "")
        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._expires_at: float = 0
        # Bez zámku sa paralelné requesty pokúsia obnoviť token naraz tým istým
        # refresh tokenom. Withings druhý pokus odmietne so statusom 601
        # ("Same arguments in less than 10 seconds") a requesty spadnú na 500.
        self._refresh_lock = asyncio.Lock()
        self._load_tokens()

    # ------------------------------------------------------------------
    # OAuth
    # ------------------------------------------------------------------

    @property
    def is_authenticated(self) -> bool:
        return bool(self._refresh_token)

    def get_authorize_url(self, state: str = "medicalai") -> str:
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "scope": "user.metrics,user.activity,user.info",
            "redirect_uri": self.redirect_uri,
            "state": state,
        }
        return f"{ACCOUNT_BASE}/oauth2_user/authorize2?{urlencode(params)}"

    async def exchange_code(self, code: str) -> bool:
        """Authorization code platí len 30 sekúnd - volaj hneď v callbacku."""
        return await self._request_token({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
        })

    async def _refresh(self) -> bool:
        if not self._refresh_token:
            return False
        return await self._request_token({
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
        })

    async def _request_token(self, payload: Dict[str, str]) -> bool:
        data = {
            # POZOR: Withings vyžaduje action=requesttoken. Bez neho vráti 503.
            "action": "requesttoken",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            **payload,
        }
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(f"{API_BASE}/v2/oauth2", data=data)

        body = resp.json()
        if body.get("status") != 0:
            print(f"[WITHINGS] Token request zlyhal: {body}")
            return False

        b = body["body"]
        self._access_token = b["access_token"]
        # Refresh token ROTUJE - starý okamžite prestane platiť.
        self._refresh_token = b["refresh_token"]
        self._expires_at = time.time() + int(b["expires_in"]) - 300
        self._save_tokens(b.get("userid"))
        print(f"[WITHINGS] Autentifikované, userid={b.get('userid')}")
        return True

    def _load_tokens(self) -> None:
        """Najprv databáza, potom súbor. Súborová vetva slúži aj na migráciu."""
        engine = _db()
        if engine is not None:
            try:
                with engine.begin() as conn:
                    row = conn.execute(text(
                        "SELECT access_token, refresh_token, expires_at "
                        "FROM withings_tokens WHERE id = 1"
                    )).first()
                if row:
                    self._access_token = row[0]
                    self._refresh_token = row[1]
                    self._expires_at = float(row[2])
                    print("[WITHINGS] Tokeny načítané z databázy")
                    return
            except Exception as e:
                print(f"[WITHINGS] Tokeny sa nepodarilo načítať z DB: {e}")

        if not TOKEN_FILE.exists():
            return
        try:
            data = json.loads(TOKEN_FILE.read_text())
            self._access_token = data.get("access_token")
            self._refresh_token = data.get("refresh_token")
            self._expires_at = data.get("expires_at", 0)
            print("[WITHINGS] Tokeny načítané zo súboru")
            if engine is not None and self._refresh_token:
                # Prenesieme starý súborový token do DB, nech netreba
                # autorizovať znova.
                self._save_tokens(data.get("userid"))
        except Exception as e:
            print(f"[WITHINGS] Tokeny sa nepodarilo načítať: {e}")

    def _save_tokens(self, userid: Optional[int] = None) -> None:
        engine = _db()
        if engine is not None:
            try:
                with engine.begin() as conn:
                    conn.execute(text("""
                        INSERT INTO withings_tokens
                            (id, withings_user_id, access_token, refresh_token,
                             expires_at, updated_at)
                        VALUES (1, :uid, :at, :rt, :exp, now())
                        ON CONFLICT (id) DO UPDATE SET
                            withings_user_id = COALESCE(
                                EXCLUDED.withings_user_id,
                                withings_tokens.withings_user_id
                            ),
                            access_token  = EXCLUDED.access_token,
                            refresh_token = EXCLUDED.refresh_token,
                            expires_at    = EXCLUDED.expires_at,
                            updated_at    = now()
                    """), {
                        "uid": userid,
                        "at": self._access_token,
                        "rt": self._refresh_token,
                        "exp": self._expires_at,
                    })
                return
            except Exception as e:
                # Pád zápisu do DB nesmie zhodiť autorizáciu - spadneme na súbor.
                print(f"[WITHINGS] Tokeny sa nepodarilo uložiť do DB: {e}")

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(json.dumps({
            "access_token": self._access_token,
            "refresh_token": self._refresh_token,
            "expires_at": self._expires_at,
            "userid": userid,
        }))
        # Tokeny k zdravotným dátam - nenechávaj ich čitateľné pre kohokoľvek
        try:
            os.chmod(TOKEN_FILE, 0o600)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    async def _refresh_locked(self, stale_token: Optional[str]) -> bool:
        """
        Obnoví token, ale len ak ho medzitým neobnovil niekto iný.

        Stránka volá /sleep, /activity, /measures a /ecg naraz. Keby každý
        z nich spustil vlastný refresh, Withings by tri z nich odmietol -
        refresh token totiž pri každom použití rotuje.
        """
        async with self._refresh_lock:
            if self._access_token and self._access_token != stale_token:
                return True  # obnovil ho iný request, kým sme čakali
            return await self._refresh()

    async def _call(self, path: str, action: str, **params) -> Dict[str, Any]:
        token = self._access_token
        if not token or time.time() >= self._expires_at:
            if not await self._refresh_locked(token):
                raise RuntimeError("Withings: token vypršal, treba znovu autorizovať")
            token = self._access_token

        payload = {"action": action,
                   **{k: v for k, v in params.items() if v is not None}}

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{API_BASE}{path}",
                data=payload,
                headers={"Authorization": f"Bearer {token}"},
            )

        body = resp.json()
        status = body.get("status")

        # Withings vracia HTTP 200 aj pri chybe - status je v tele odpovede
        if status == 401:
            if await self._refresh_locked(token):
                return await self._call(path, action, **params)
            raise RuntimeError("Withings: neplatný token")

        if status != 0:
            raise RuntimeError(f"Withings {path}/{action}: status={status} "
                               f"error={body.get('error')}")

        return body.get("body", {})

    # ------------------------------------------------------------------
    # Dáta
    # ------------------------------------------------------------------

    async def get_measures(self, days: int = 30,
                           meastypes: Optional[List[int]] = None) -> List[Dict]:
        """Merania z hodiniek a váhy (tep, SpO2, teplota, váha, bioimpedancia)."""
        since = int((datetime.now() - timedelta(days=days)).timestamp())
        body = await self._call(
            "/measure", "getmeas",
            meastypes=",".join(map(str, meastypes or WATCH_MEASTYPES)),
            lastupdate=since, category=1,
        )

        out: List[Dict] = []
        for grp in body.get("measuregrps", []):
            ts = _iso(grp["date"])
            for m in grp.get("measures", []):
                mapping = MEASTYPES.get(m["type"])
                if not mapping:
                    continue
                name, unit = mapping
                out.append({
                    "metric": name,
                    "value": float(m["value"]) * (10 ** int(m["unit"])),
                    "unit": unit,
                    "measured_at": ts,
                    "device_id": grp.get("deviceid"),
                    "manual": grp.get("attrib") in (1, 2, 4),
                })
        return out

    async def get_sleep(self, days: int = 30) -> List[Dict]:
        """
        Spánok z hodiniek. TOTO CEZ getmeas NEPRÍDE - má vlastný endpoint.

        Pozor: sleep_score a dĺžka z API sa líšia od toho, čo ukazuje Health
        Mate (býva nižšie). Appka počíta vlastným algoritmom. Neporovnávaj ich.
        """
        since = int((datetime.now() - timedelta(days=days)).timestamp())
        body = await self._call(
            "/v2/sleep", "getsummary", lastupdate=since,
            data_fields=("total_sleep_time,sleep_efficiency,sleep_latency,"
                         "wakeupduration,wakeupcount,lightsleepduration,"
                         "deepsleepduration,remsleepduration,hr_average,hr_min,"
                         "hr_max,rr_average,breathing_disturbances_intensity,"
                         "snoring,sleep_score,apnea_hypopnea_index"),
        )

        out = []
        for s in body.get("series", []):
            d = s.get("data", {})
            light = d.get("lightsleepduration") or 0
            deep = d.get("deepsleepduration") or 0
            rem = d.get("remsleepduration") or 0
            out.append({
                "date": datetime.fromtimestamp(
                    s["startdate"], tz=timezone.utc
                ).strftime("%Y-%m-%d"),
                "from": _iso(s["startdate"]),
                "to": _iso(s["enddate"]),
                "total_sleep_seconds": d.get("total_sleep_time") or (light + deep + rem),
                "light_seconds": light,
                "deep_seconds": deep,
                "rem_seconds": rem,
                "awake_seconds": d.get("wakeupduration"),
                "wakeup_count": d.get("wakeupcount"),
                "efficiency": d.get("sleep_efficiency"),
                "hr_average": d.get("hr_average"),
                "hr_min": d.get("hr_min"),
                "hr_max": d.get("hr_max"),
                "respiration_rate": d.get("rr_average"),
                "snoring_seconds": d.get("snoring"),
                "breathing_disturbances": d.get("breathing_disturbances_intensity"),
                "apnea_hypopnea_index": d.get("apnea_hypopnea_index"),
                "sleep_score": d.get("sleep_score"),
            })
        return out

    async def get_sleep_hrv(self, nights: int = 14) -> List[Dict]:
        """
        Variabilita srdcovej frekvencie a minútové rady zo spánku.

        Pozor: toto NIE JE getsummary, ktorý dáva jedno číslo za noc. Endpoint
        /v2/sleep s action=get vracia hodnoty po minútach - rmssd, sdnn_1,
        tep, dychovú frekvenciu a skóre pohybu v posteli.

        rmssd  = druhá odmocnina priemeru štvorcov rozdielov po sebe idúcich
                 NN intervalov, počítané cez niekoľko sekúnd
        sdnn_1 = smerodajná odchýlka NN intervalov za jednu minútu

        Volá sa po jednej noci, lebo endpoint má obmedzené časové okno. Pri
        14 nociach je to 14 requestov - limit 120/min platí pre celú aplikáciu,
        takže neťahaj stovky nocí naraz.
        """
        since = int((datetime.now() - timedelta(days=nights + 1)).timestamp())
        summary = await self._call("/v2/sleep", "getsummary", lastupdate=since)

        periods = sorted(
            summary.get("series", []),
            key=lambda x: x["startdate"],
        )[-nights:]

        out: List[Dict] = []
        for night in periods:
            try:
                body = await self._call(
                    "/v2/sleep", "get",
                    startdate=night["startdate"], enddate=night["enddate"],
                    data_fields="hr,rr,sdnn_1,rmssd,mvt_score,snoring",
                )
            except Exception as e:
                print(f"[WITHINGS] HRV pre noc {night['startdate']} zlyhalo: {e}")
                continue

            # Kľúčom je časová značka, nie poradie. Withings vracia noc
            # rozdelenú na segmenty, ktoré sa vedia časovo prekrývať - bez
            # deduplikácie by sa tá istá minúta započítala viackrát a počet
            # vzoriek by prekročil dĺžku noci.
            by_ts: Dict[str, Dict[int, float]] = {
                "hr": {}, "rr": {}, "sdnn_1": {}, "rmssd": {}, "mvt_score": {},
            }

            for seg in body.get("series", []):
                for field in by_ts:
                    raw = seg.get(field)
                    if not isinstance(raw, dict):
                        continue
                    for ts, val in raw.items():
                        if val is None:
                            continue
                        value = float(val)
                        # Nulová variabilita je fyziologicky nemožná - sú to
                        # artefakty z momentov bez dobrého kontaktu so zápästím.
                        # Ak by sme ich nechali, ťahali by priemer nadol.
                        if field in ("sdnn_1", "rmssd") and value <= 0:
                            continue
                        by_ts[field][int(ts)] = value

            buckets: Dict[str, List[float]] = {
                k: list(v.values()) for k, v in by_ts.items()
            }
            rmssd_series: List[List[float]] = sorted(
                ([ts, val] for ts, val in by_ts["rmssd"].items()),
                key=lambda x: x[0],
            )

            if not buckets["rmssd"] and not buckets["sdnn_1"]:
                continue

            agg = lambda v: {  # noqa: E731
                "avg": round(statistics.fmean(v), 1) if v else None,
                "median": round(statistics.median(v), 1) if v else None,
                "min": round(min(v), 1) if v else None,
                "max": round(max(v), 1) if v else None,
            }

            out.append({
                "date": datetime.fromtimestamp(
                    night["startdate"], tz=timezone.utc
                ).strftime("%Y-%m-%d"),
                "from": _iso(night["startdate"]),
                "to": _iso(night["enddate"]),
                "rmssd": agg(buckets["rmssd"]),
                "sdnn": agg(buckets["sdnn_1"]),
                "hr": agg(buckets["hr"]),
                "respiration": agg(buckets["rr"]),
                "movement": agg(buckets["mvt_score"]),
                "samples": len(buckets["rmssd"]),
                # Krivku posielame len pre poslednú noc, inak by odpoveď
                # narástla o stovky bodov na každú noc.
                "series": rmssd_series if night is periods[-1] else None,
            })

        return out

    async def get_activity(self, days: int = 30) -> List[Dict]:
        """Denná aktivita. Tiež vlastný endpoint, cez getmeas nepríde."""
        since = int((datetime.now() - timedelta(days=days)).timestamp())
        body = await self._call(
            "/v2/measure", "getactivity", lastupdate=since,
            data_fields=("steps,distance,elevation,soft,moderate,intense,active,"
                         "calories,totalcalories,hr_average,hr_min,hr_max,"
                         "hr_zone_0,hr_zone_1,hr_zone_2,hr_zone_3"),
        )

        return [{
            "date": a["date"],
            "total_steps": a.get("steps"),
            "distance_m": a.get("distance"),
            "elevation_m": a.get("elevation"),
            "calories_active": a.get("calories"),
            "calories_total": a.get("totalcalories"),
            # POZOR: soft/moderate/intense chodia zo Withings v SEKUNDÁCH,
            # nie v minútach. 11237 nie je 187 hodín, ale 3 h 7 min.
            "seconds_soft": a.get("soft"),
            "seconds_moderate": a.get("moderate"),
            "seconds_intense": a.get("intense"),
            "hr_average": a.get("hr_average"),
            "hr_min": a.get("hr_min"),
            "hr_max": a.get("hr_max"),
            # Rovnako sekundy strávené v jednotlivých pásmach tepu.
            "hr_zone_seconds": [a.get(f"hr_zone_{i}") for i in range(4)],
        } for a in body.get("activities", [])]

    async def get_ecg(self, with_signal: bool = False) -> List[Dict]:
        """
        EKG záznamy.

        startdate=0 je zámerne: /v2/heart list u viacerých ľudí vracia prázdno,
        keď je startdate nenulový. Záznamov je málo, filtruj si až lokálne.

        Klasifikácia (afib) pochádza z certifikovaného algoritmu Withings
        (CE v EU, FDA clearance v US). Nikdy ju neprepisuj vlastným modelom.
        """
        body = await self._call("/v2/heart", "list", startdate=0,
                                enddate=int(time.time()))

        out = []
        for rec in body.get("series", []):
            ecg = rec.get("ecg") or {}
            item = {
                "recorded_at": _iso(rec["timestamp"]),
                "signal_id": ecg.get("signalid"),
                "afib_classification": ecg.get("afib"),
                "heart_rate": rec.get("heart_rate"),
                "device_model": rec.get("model"),
            }
            if with_signal and ecg.get("signalid"):
                sig = await self._call("/v2/heart", "get",
                                       signalid=ecg["signalid"])
                item["signal"] = sig.get("signal")
                item["sampling_frequency"] = sig.get("sampling_frequency")
                item["wear_position"] = sig.get("wearposition")
            out.append(item)
        return out

    async def get_historical_data(self, days: int = 30) -> Dict[str, Any]:
        """Všetko naraz - rovnaký tvar ako garmin.get_historical_data()."""
        return {
            "measures": await self.get_measures(days),
            "sleep": await self.get_sleep(days),
            "activity": await self.get_activity(days),
            "ecg": await self.get_ecg(),
            "hrv": await self.get_sleep_hrv(),
            "synced_at": datetime.now().isoformat(),
            "days": days,
        }


_connector: Optional[WithingsConnector] = None


def get_withings_connector() -> WithingsConnector:
    global _connector
    if _connector is None:
        _connector = WithingsConnector()
    return _connector
