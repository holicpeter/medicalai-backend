"""Create (or reset) the promo demo account with made-up data.

Everything here is fictional — the person, the clinic, the doctor and every
value. It exists so the app can be shown to someone without showing anyone's
real health data: log in as the demo user and every screen has something to
show (dashboard, trends, risks, uploaded reports the chat can quote, Apple
Health activity and sleep, a nutrition diary, family history, a chat
conversation).

The story the data tells: Martin, 47, found prediabetes-range glucose and
high LDL in January, with a father who had type 2 diabetes and a heart
attack. He changed his diet and started walking; by September his glucose,
HbA1c, LDL, weight and blood pressure are all trending down. That gives the
trend, risk and chat features something real-looking to work with.

Dates are relative to the day the script runs, so the demo never looks
stale — re-run it now and then. Re-running deletes the demo account with all
its data (including anything visitors added) and creates it fresh.

Usage (Railway):
    railway ssh --service web
    DEMO_PASSWORD='…' python -m scripts.seed_demo_account

Visitors do not need the password: "Try the demo" calls
POST /api/auth/demo-login, and the account is read-only (app/auth/demo.py).
DEMO_EMAIL defaults to settings.DEMO_EMAIL and must be listed in DEMO_EMAILS
(it is by default) — that is what makes it read-only. Without DEMO_PASSWORD
a random one is generated and printed once; it is only needed to log in
through the normal form.
"""
import os
import random
import secrets
import sys
from datetime import date, datetime, time, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.auth.account_deletion import delete_account  # noqa: E402
from app.auth.security import hash_password  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import (  # noqa: E402
    AppleHealthData,
    ChatMessage,
    Document,
    DocumentChunk,
    FamilyMember,
    HealthRecord,
    NutritionEntry,
    Patient,
    User,
    get_session,
    init_database,
)

# Fixed seed: the same demo every time, only shifted to today's date.
rng = random.Random(47)

CLINIC = "Ambulancia všeobecného lekára (ukážková)"
DOCTOR = "MUDr. Ukážková Lekárka"
BATCH = "demo-seed"


_MONTHS = ["január", "február", "marec", "apríl", "máj", "jún", "júl",
           "august", "september", "október", "november", "december"]
_MONTHS_GEN = ["januáru", "februáru", "marcu", "aprílu", "máju", "júnu", "júlu",
               "augustu", "septembru", "októbru", "novembru", "decembru"]
_ASCII = str.maketrans("áäčďéíĺľňóôŕšťúýž", "aacdeillnoorstuyz")


def _months(today: date):
    """Month names of the three blood draws, so the texts match whenever the script runs."""
    first, second, third = (today - timedelta(days=n) for n in (240, 120, 12))
    return {
        "m1": _MONTHS[first.month - 1], "m2": _MONTHS[second.month - 1], "m3": _MONTHS[third.month - 1],
        "m1_since": _MONTHS_GEN[first.month - 1],
    }


def _lab_reports(today: date):
    """Three blood panels, eight and four months apart, improving over time."""
    m = _months(today)

    def filename(month: str) -> str:
        return f"odber-krvi-{month.translate(_ASCII)}.pdf"

    return [
        {
            "date": today - timedelta(days=240),
            "filename": filename(m["m1"]),
            "values": {
                "glucose": ("6.3", "mmol/l", "3.9-5.6", False),
                "hba1c": ("6.1", "%", "4.0-5.6", False),
                "cholesterol": ("6.4", "mmol/l", "<5.2", False),
                "ldl": ("4.2", "mmol/l", "<3.0", False),
                "hdl": ("1.05", "mmol/l", ">1.0", True),
                "triglycerides": ("2.1", "mmol/l", "<1.7", False),
                "alt": ("0.72", "µkat/l", "0.10-0.78", True),
                "creatinine": ("88", "µmol/l", "62-106", True),
                "hemoglobin": ("152", "g/l", "135-175", True),
                "crp": ("3.1", "mg/l", "<5", True),
            },
            "conclusion": (
                "Glykémia nalačno 6,3 mmol/l a HbA1c 6,1 % zodpovedajú prediabetu. "
                "Zvýšený celkový cholesterol a LDL, hraničné triglyceridy. Pečeňové a "
                "obličkové parametre v norme. Pacient s pozitívnou rodinnou anamnézou "
                "(otec DM 2. typu, infarkt myokardu)."
            ),
            "recommendation": (
                "Úprava stravy s obmedzením jednoduchých cukrov a nasýtených tukov, "
                "pravidelný pohyb aspoň 150 minút týždenne, redukcia hmotnosti o 5–7 %. "
                "Domáce meranie krvného tlaku. Kontrola krvného obrazu a lipidov o 4 mesiace."
            ),
        },
        {
            "date": today - timedelta(days=120),
            "filename": filename(m["m2"]),
            "values": {
                "glucose": ("5.9", "mmol/l", "3.9-5.6", False),
                "hba1c": ("5.9", "%", "4.0-5.6", False),
                "cholesterol": ("5.7", "mmol/l", "<5.2", False),
                "ldl": ("3.6", "mmol/l", "<3.0", False),
                "hdl": ("1.12", "mmol/l", ">1.0", True),
                "triglycerides": ("1.8", "mmol/l", "<1.7", False),
                "alt": ("0.61", "µkat/l", "0.10-0.78", True),
                "creatinine": ("86", "µmol/l", "62-106", True),
                "hemoglobin": ("150", "g/l", "135-175", True),
                "crp": ("2.4", "mg/l", "<5", True),
            },
            "conclusion": (
                "Priaznivý vývoj po zmene životosprávy: glykémia 5,9 mmol/l, HbA1c 5,9 %, "
                "LDL pokles zo 4,2 na 3,6 mmol/l. Hmotnosť −3 kg. Naďalej v pásme prediabetu."
            ),
            "recommendation": (
                "Pokračovať v nastavenom režime, doplniť silový tréning 2× týždenne. "
                "Kontrola o 4 mesiace, pri LDL nad 3,0 zvážiť liečbu statínom."
            ),
        },
        {
            "date": today - timedelta(days=12),
            "filename": filename(m["m3"]),
            "values": {
                "glucose": ("5.6", "mmol/l", "3.9-5.6", True),
                "hba1c": ("5.7", "%", "4.0-5.6", False),
                "cholesterol": ("5.2", "mmol/l", "<5.2", True),
                "ldl": ("3.1", "mmol/l", "<3.0", False),
                "hdl": ("1.21", "mmol/l", ">1.0", True),
                "triglycerides": ("1.5", "mmol/l", "<1.7", True),
                "alt": ("0.55", "µkat/l", "0.10-0.78", True),
                "creatinine": ("85", "µmol/l", "62-106", True),
                "hemoglobin": ("151", "g/l", "135-175", True),
                "crp": ("1.6", "mg/l", "<5", True),
            },
            "conclusion": (
                "Glykémia nalačno 5,6 mmol/l na hornej hranici normy, HbA1c 5,7 % tesne "
                "nad normou. LDL 3,1 mmol/l, triglyceridy v norme. Celkovo výrazné "
                f"zlepšenie oproti {m['m1_since']}."
            ),
            "recommendation": (
                "Statín zatiaľ nie je potrebný. Pokračovať v režime, kontrola lipidov "
                "a HbA1c o 6 mesiacov. Preventívna prehliadka o rok."
            ),
        },
    ]


_LABELS = {
    "glucose": "Glukóza nalačno",
    "hba1c": "HbA1c",
    "cholesterol": "Celkový cholesterol",
    "ldl": "LDL cholesterol",
    "hdl": "HDL cholesterol",
    "triglycerides": "Triglyceridy",
    "alt": "ALT",
    "creatinine": "Kreatinín",
    "hemoglobin": "Hemoglobín",
    "crp": "CRP",
}


def _report_text(report) -> str:
    rows = "\n".join(
        f"{_LABELS[k]}: {v} {unit} (ref. {ref}){'' if ok else '  ↑'}"
        for k, (v, unit, ref, ok) in report["values"].items()
    )
    return (
        f"{CLINIC}\n{DOCTOR}\n"
        f"Výsledky laboratórneho vyšetrenia zo dňa {report['date']:%d. %m. %Y}\n"
        f"Pacient: Martin Ukážkový, nar. {report['date'].year - 47} (ukážkové údaje)\n\n"
        f"{rows}\n\n"
        f"Záver: {report['conclusion']}\n\n"
        f"Odporúčanie: {report['recommendation']}\n"
    )


def _blood_pressure(today: date):
    """Home readings twice a week for eight months, drifting from ~138/88 to ~125/80."""
    days = 240
    out = []
    d = days
    while d >= 0:
        progress = 1 - d / days
        sys_ = round(139 - 13 * progress + rng.gauss(0, 3))
        dia = round(88 - 8 * progress + rng.gauss(0, 2))
        pulse = round(76 - 8 * progress + rng.gauss(0, 3))
        out.append((today - timedelta(days=d), f"{sys_}/{dia}", pulse))
        d -= rng.choice((3, 4))
    return out


def _weights(today: date):
    days = 240
    return [
        (today - timedelta(days=d), round(92.4 - 6.2 * (1 - d / days) + rng.gauss(0, 0.35), 1))
        for d in range(days, -1, -7)
    ]


def _apple_health(patient_id: int, today: date):
    """90 days of steps, resting heart rate, heart rate samples and sleep."""
    rows = []
    source = dict(
        source_name="iPhone (ukážka)",
        device_name="iPhone",
        device_manufacturer="Apple Inc.",
        import_batch_id=BATCH,
        patient_id=patient_id,
    )
    for d in range(90, -1, -1):
        day = today - timedelta(days=d)
        progress = 1 - d / 90
        weekend = day.weekday() >= 5
        steps = max(1500, int(rng.gauss(8200 + 2200 * progress + (1500 if weekend else 0), 1600)))
        start = datetime.combine(day, time(20, 0))
        if d == 0:
            # Today is still in progress.
            steps = int(steps * 0.55)
            start = datetime.combine(day, time(13, 0))
        rows.append(AppleHealthData(
            record_type="HKQuantityTypeIdentifierStepCount", value=steps, unit="count",
            start_date=start, end_date=start + timedelta(minutes=5), **source,
        ))
        rows.append(AppleHealthData(
            record_type="HKQuantityTypeIdentifierDistanceWalkingRunning",
            value=round(steps * 0.00074, 2), unit="km",
            start_date=start, end_date=start + timedelta(minutes=5), **source,
        ))
        resting = round(66 - 6 * progress + rng.gauss(0, 1.5))
        rows.append(AppleHealthData(
            record_type="HKQuantityTypeIdentifierRestingHeartRate", value=resting, unit="count/min",
            start_date=datetime.combine(day, time(7, 30)), end_date=datetime.combine(day, time(7, 31)),
            **source,
        ))
        for hour, extra in ((8, 8), (12, 14), (17, 30 if not weekend else 45), (21, 4)):
            t = datetime.combine(day, time(hour, rng.randint(0, 59)))
            rows.append(AppleHealthData(
                record_type="HKQuantityTypeIdentifierHeartRate",
                value=round(resting + extra + rng.gauss(0, 4)), unit="count/min",
                start_date=t, end_date=t, **source,
            ))
        if d > 0:
            bed = datetime.combine(day, time(22, 30)) + timedelta(minutes=rng.randint(-20, 70))
            hours = rng.gauss(7.1 if weekend else 6.8, 0.45)
            rows.append(AppleHealthData(
                record_type="HKCategoryTypeIdentifierSleepAnalysis", value=0, unit="",
                start_date=bed, end_date=bed + timedelta(hours=hours), **source,
            ))
    return rows


_MEALS = [
    ("Raňajky", [("Ovsená kaša s čučoriedkami", 250, 290, 9, 48, 7), ("Biely jogurt", 150, 95, 8, 6, 4)]),
    ("Raňajky", [("Celozrnný chlieb", 80, 190, 7, 34, 2), ("Vajíčka natvrdo", 120, 185, 15, 1, 13), ("Paradajka", 100, 18, 1, 4, 0)]),
    ("Obed", [("Kuracie prsia na grile", 150, 250, 46, 0, 6), ("Hnedá ryža", 180, 200, 4, 42, 2), ("Zeleninový šalát", 150, 60, 2, 8, 3)]),
    ("Obed", [("Šošovicová polievka", 350, 260, 16, 38, 5), ("Celozrnný chlieb", 50, 120, 4, 21, 1)]),
    ("Obed", [("Pečený losos", 140, 290, 30, 0, 18), ("Varené zemiaky", 200, 155, 4, 34, 0), ("Brokolica", 120, 40, 3, 7, 0)]),
    ("Večera", [("Grécky šalát s fetou", 300, 320, 11, 14, 25)]),
    ("Večera", [("Tvarohová nátierka", 100, 150, 14, 4, 8), ("Celozrnný chlieb", 80, 190, 7, 34, 2), ("Paprika", 100, 30, 1, 6, 0)]),
    ("Desiata", [("Jablko", 180, 95, 0, 25, 0), ("Vlašské orechy", 25, 165, 4, 3, 16)]),
]

_MEAL_TIMES = {"Raňajky": time(7, 20), "Desiata": time(10, 15), "Obed": time(12, 30), "Večera": time(18, 45)}


def _nutrition(patient_id: int, today: date):
    rows = []
    for d in range(6, -1, -1):
        day = today - timedelta(days=d)
        picks = [
            rng.choice([m for m in _MEALS if m[0] == "Raňajky"]),
            rng.choice([m for m in _MEALS if m[0] == "Desiata"]),
            rng.choice([m for m in _MEALS if m[0] == "Obed"]),
            rng.choice([m for m in _MEALS if m[0] == "Večera"]),
        ]
        if d == 0:
            picks = picks[:3]  # today's dinner has not happened yet
        for kind, items in picks:
            item_dicts = [
                {"name": n, "estimated_grams": g, "calories": c, "protein_g": p,
                 "carbs_g": ch, "fat_g": f, "confidence": 0.8}
                for n, g, c, p, ch, f in items
            ]
            rows.append(NutritionEntry(
                patient_id=patient_id,
                logged_at=datetime.combine(day, _MEAL_TIMES[kind]),
                items=item_dicts,
                total_calories=sum(i["calories"] for i in item_dicts),
                total_protein_g=sum(i["protein_g"] for i in item_dicts),
                total_carbs_g=sum(i["carbs_g"] for i in item_dicts),
                total_fat_g=sum(i["fat_g"] for i in item_dicts),
                overall_confidence=0.8,
                recommendation="Vyvážené jedlo s dostatkom bielkovín a vlákniny.",
                notes=kind,
            ))
    return rows


def _family(patient_id: int):
    return [
        FamilyMember(
            patient_id=patient_id, first_name="Otec", last_name="", relationship_type="father",
            gender="male", date_of_birth=date(1948, 3, 12), date_of_death=date(2019, 11, 2),
            chronic_conditions=["Diabetes mellitus 2. typu", "Hypertenzia"],
            genetic_conditions=[], allergies=[], medications=[], surgeries=[],
            smoking=True, smoking_years=25, cause_of_death="Infarkt myokardu",
        ),
        FamilyMember(
            patient_id=patient_id, first_name="Matka", last_name="", relationship_type="mother",
            gender="female", date_of_birth=date(1951, 7, 30),
            chronic_conditions=["Hypertenzia", "Hypotyreóza"],
            genetic_conditions=[], allergies=[], medications=[], surgeries=[],
        ),
        FamilyMember(
            patient_id=patient_id, first_name="Brat", last_name="", relationship_type="brother",
            gender="male", date_of_birth=date(1975, 5, 5),
            chronic_conditions=["Zvýšený cholesterol"],
            genetic_conditions=[], allergies=[], medications=[], surgeries=[],
        ),
        FamilyMember(
            patient_id=patient_id, first_name="Babka", last_name="", relationship_type="grandmother",
            gender="female",
            chronic_conditions=[], genetic_conditions=["Rakovina prsníka"],
            allergies=[], medications=[], surgeries=[],
            notes="Z matkinej strany.",
        ),
    ]


def _chat(patient_id: int, today: date):
    m = _months(today)
    t0 = datetime.combine(today - timedelta(days=11), time(19, 5))
    turns = [
        ("user", f"Ako sa mi zmenili hodnoty cukru od {m['m1_since'][:-1]}a?"),
        ("assistant",
         f"Glykémia nalačno klesla zo 6,3 mmol/l ({m['m1']}) cez 5,9 ({m['m2']}) na 5,6 mmol/l "
         "v poslednom odbere — to je horná hranica normy. HbA1c sa znížil zo 6,1 % na 5,7 %, "
         "čo je stále tesne nad normou (do 5,6 %), ale vývoj je jednoznačne priaznivý. "
         f"Podľa záveru z posledného odberu ({m['m3']}) ide o výrazné zlepšenie oproti {m['m1_since']}. "
         "Ďalší postup konzultujte so svojou lekárkou."),
        ("user", "Musím brať lieky na cholesterol?"),
        ("assistant",
         "Podľa poslednej správy statín zatiaľ nie je potrebný — LDL kleslo zo 4,2 na "
         "3,1 mmol/l a lekárka odporúča pokračovať v režime a skontrolovať lipidy o 6 mesiacov. "
         f"V správe z mesiaca {m['m2']} bola poznámka, že pri LDL nad 3,0 by sa liečba zvažovala, takže o tom "
         "rozhodne pri ďalšej kontrole. Vzhľadom na otca s infarktom je dobré sa na to "
         "pri kontrole priamo opýtať."),
        ("user", "Koľko teraz chodím denne?"),
        ("assistant",
         "Za posledných 7 dní máte priemerne okolo 10 000 krokov denne, cez víkendy viac. "
         "Pred tromi mesiacmi to bolo zhruba 8 000. Pokojový tep sa za ten čas znížil "
         "približne zo 66 na 60 úderov za minútu, čo zodpovedá lepšej kondícii."),
    ]
    return [
        ChatMessage(patient_id=patient_id, role=role, content=text, created_at=t0 + timedelta(minutes=2 * i))
        for i, (role, text) in enumerate(turns)
    ]


def seed(email: str, password: str) -> dict:
    # Same demo on every run, whatever ran before in this process.
    rng.seed(47)
    init_database()
    today = date.today()

    session = get_session()
    try:
        existing = session.query(User).filter_by(email=email).first()
        existing_id = existing.id if existing else None
    finally:
        session.close()
    if existing_id is not None:
        delete_account(existing_id)

    session = get_session()
    try:
        user = User(
            email=email,
            password_hash=hash_password(password),
            gdpr_consent_at=datetime.now(),
            is_active=True,
        )
        session.add(user)
        session.flush()

        patient = Patient(
            user_id=user.id, first_name="Martin", last_name="Ukážkový",
            date_of_birth=date(today.year - 47, 4, 18), gender="male",
            blood_type="A+", height_cm=182, email=email,
        )
        session.add(patient)
        session.flush()
        pid = patient.id

        counts = {"health_records": 0}
        for report in _lab_reports(today):
            text = _report_text(report)
            doc = Document(
                patient_id=pid, filename=report["filename"], file_path="",
                file_type="pdf", file_size_bytes=len(text.encode()) * 40,
                ocr_processed=True, ocr_text=text, processing_status="completed",
                document_type="lab_report", document_date=report["date"],
                uploaded_at=datetime.combine(report["date"] + timedelta(days=1), time(18, 30)),
                processed_at=datetime.combine(report["date"] + timedelta(days=1), time(18, 31)),
            )
            session.add(doc)
            session.flush()
            paragraphs = [p for p in text.split("\n\n") if p.strip()]
            for i, chunk in enumerate(paragraphs):
                session.add(DocumentChunk(document_id=doc.id, chunk_index=i, text=chunk))
            for metric, (value, unit, ref, ok) in report["values"].items():
                session.add(HealthRecord(
                    patient_id=pid, record_type="lab", record_date=report["date"],
                    source="ocr", source_file=report["filename"], metric_type=metric,
                    value=value, unit=unit, reference_range=ref, is_normal=ok,
                    interpretation="normal" if ok else "high",
                    doctor_name=DOCTOR, facility_name=CLINIC,
                    created_at=doc.uploaded_at,
                ))
                counts["health_records"] += 1

        for day, bp, pulse in _blood_pressure(today):
            session.add(HealthRecord(
                patient_id=pid, record_type="vital", record_date=day, source="manual",
                metric_type="blood_pressure", value=bp, unit="mmHg",
            ))
            session.add(HealthRecord(
                patient_id=pid, record_type="vital", record_date=day, source="manual",
                metric_type="heart_rate", value=str(pulse), unit="bpm",
            ))
            counts["health_records"] += 2
        for day, kg in _weights(today):
            session.add(HealthRecord(
                patient_id=pid, record_type="vital", record_date=day, source="manual",
                metric_type="weight", value=str(kg), unit="kg",
            ))
            session.add(HealthRecord(
                patient_id=pid, record_type="vital", record_date=day, source="manual",
                metric_type="bmi", value=str(round(kg / 1.82 ** 2, 1)), unit="kg/m2",
            ))
            counts["health_records"] += 2

        apple = _apple_health(pid, today)
        nutrition = _nutrition(pid, today)
        family = _family(pid)
        chat = _chat(pid, today)
        session.add_all(apple + nutrition + family + chat)
        session.commit()

        counts.update({
            "documents": 3,
            "apple_health_data": len(apple),
            "nutrition_entries": len(nutrition),
            "family_members": len(family),
            "chat_messages": len(chat),
        })
        return {"user_id": user.id, "patient_id": pid, **counts}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def main() -> int:
    email = (os.environ.get("DEMO_EMAIL") or settings.DEMO_EMAIL).strip().lower()
    password = os.environ.get("DEMO_PASSWORD", "")
    generated = False
    if not password:
        password = secrets.token_urlsafe(12)
        generated = True
    if len(password) < 10:
        print("ERROR: DEMO_PASSWORD must be at least 10 characters.", file=sys.stderr)
        return 1

    result = seed(email, password)
    print(f"Demo account ready: {email}")
    print("  " + ", ".join(f"{k}={v}" for k, v in result.items()))
    if generated:
        print(f"  password (generated, shown once): {password}")
    if email not in {e.lower() for e in settings.DEMO_EMAILS}:
        print(f"\nWARNING: {email} is not in DEMO_EMAILS — visitors could delete this account.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
