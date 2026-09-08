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
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx

API_BASE = "https://wbsapi.withings.net"
ACCOUNT_BASE = "https://account.withings.com"

DATA_DIR = Path("data/withings")
TOKEN_FILE = DATA_DIR / "tokens.json"

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


class WithingsConnector:
    def __init__(self):
        self.client_id = os.environ.get("WITHINGS_CLIENT_ID", "")
        self.client_secret = os.environ.get("WITHINGS_CLIENT_SECRET", "")
        self.redirect_uri = os.environ.get("WITHINGS_REDIRECT_URI", "")
        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._expires_at: float = 0
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
        if not TOKEN_FILE.exists():
            return
        try:
            data = json.loads(TOKEN_FILE.read_text())
            self._access_token = data.get("access_token")
            self._refresh_token = data.get("refresh_token")
            self._expires_at = data.get("expires_at", 0)
        except Exception as e:
            print(f"[WITHINGS] Tokeny sa nepodarilo načítať: {e}")

    def _save_tokens(self, userid: Optional[int] = None) -> None:
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

    async def _call(self, path: str, action: str, **params) -> Dict[str, Any]:
        if not self._access_token or time.time() >= self._expires_at:
            if not await self._refresh():
                raise RuntimeError("Withings: token vypršal, treba znovu autorizovať")

        payload = {"action": action,
                   **{k: v for k, v in params.items() if v is not None}}

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{API_BASE}{path}",
                data=payload,
                headers={"Authorization": f"Bearer {self._access_token}"},
            )

        body = resp.json()
        status = body.get("status")

        # Withings vracia HTTP 200 aj pri chybe - status je v tele odpovede
        if status == 401:
            if await self._refresh():
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
            ts = datetime.fromtimestamp(grp["date"]).isoformat()
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
                "date": datetime.fromtimestamp(s["startdate"]).strftime("%Y-%m-%d"),
                "from": datetime.fromtimestamp(s["startdate"]).isoformat(),
                "to": datetime.fromtimestamp(s["enddate"]).isoformat(),
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
            "minutes_soft": a.get("soft"),
            "minutes_moderate": a.get("moderate"),
            "minutes_intense": a.get("intense"),
            "hr_average": a.get("hr_average"),
            "hr_min": a.get("hr_min"),
            "hr_max": a.get("hr_max"),
            "hr_zones": [a.get(f"hr_zone_{i}") for i in range(4)],
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
                "recorded_at": datetime.fromtimestamp(rec["timestamp"]).isoformat(),
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
            "synced_at": datetime.now().isoformat(),
            "days": days,
        }


_connector: Optional[WithingsConnector] = None


def get_withings_connector() -> WithingsConnector:
    global _connector
    if _connector is None:
        _connector = WithingsConnector()
    return _connector
