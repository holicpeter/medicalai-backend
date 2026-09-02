"""
Apple Health Auto Import - File Watcher
Sleduje iCloud priečinok a automaticky importuje nové Health dáta.

Konfigurácia cez premenné prostredia:

    MEDICALAI_BACKEND_URL   kam posielať (default: produkčný Railway)
    MEDICALAI_PROXY_SECRET  ak backend vyžaduje X-Proxy-Secret
    MEDICALAI_EXPORTS_DIR   sledovaný priečinok

Lokálny vývoj:
    set MEDICALAI_BACKEND_URL=http://localhost:8000
"""

import gzip
import logging
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import requests
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# Default je produkcia. Predtým tu bol natvrdo localhost, takže watcher
# posielal importy na stroj, na ktorom beží, a do produkcie nikdy nič
# nedoputovalo — na Railway ostal jediný ručne nahraný batch.
BACKEND_URL = os.environ.get(
    "MEDICALAI_BACKEND_URL",
    "https://web-production-fd83c.up.railway.app",
).rstrip("/")

# Cloudflare Access chráni doménu, ale Railway hostname je verejný, takže ho
# backend zamyká na túto hlavičku. Bez nej vráti 403 (a kým premenná na Railway
# nie je nastavená, prejde to aj bez nej).
PROXY_SECRET = os.environ.get("MEDICALAI_PROXY_SECRET", "").strip()

ICLOUD_FOLDER = Path(
    os.environ.get("MEDICALAI_EXPORTS_DIR")
    or Path.home() / "iCloudDrive" / "MedicalAI" / "exports"
)

CHECK_INTERVAL = 60  # sekúnd
WATCHED_SUFFIXES = {".xml", ".csv", ".gz", ".zip"}

# Priebeh sa sleduje pollovaním, nie držaním spojenia: Railway zavrie požiadavku
# po 5 minútach bez prenosu dát, takže synchrónny import veľkého exportu
# nedobehne.
POLL_INTERVAL = 5
POLL_TIMEOUT = 3600

# Vedľa skriptu, nie v aktuálnom priečinku — inak si watcher „zabudne", čo už
# spracoval, len preto, že bol spustený odinakiaľ.
PROCESSED_LOG = Path(__file__).resolve().parent / "processed_files.txt"

# The Windows console defaults to a code page that can hold neither the emoji
# nor the Slovak diacritics, so every line came out as \U0001f680 and mojibake.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, OSError):
        pass

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('apple_health_sync.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def _headers():
    return {"X-Proxy-Secret": PROXY_SECRET} if PROXY_SECRET else {}


def _wait_until_stable(file_path: Path, timeout: int = 300) -> bool:
    """Počkaj, kým iCloud dotiahne súbor.

    Pevné sleep(5) stačilo na testovací súbor a nie na 300 MB export — čaká sa
    teda na to, kým veľkosť dvakrát po sebe zostane rovnaká.
    """
    deadline = time.time() + timeout
    last = -1
    while time.time() < deadline:
        try:
            size = file_path.stat().st_size
        except FileNotFoundError:
            return False
        if size > 0 and size == last:
            return True
        last = size
        time.sleep(2)
    logger.warning("⚠️  %s sa neprestal meniť do %ds", file_path.name, timeout)
    return False


def _compress(file_path: Path) -> tuple[Path, bool]:
    """Zgzipuj .xml pred odoslaním.

    Export je veľmi redundantný text a komprimuje sa ~27×. Bez toho veľký
    súbor nepresiahne len linku, ale aj 100 MB limit na tele požiadavky, ktorý
    Cloudflare vynucuje na edge — teda skôr, než sa požiadavka dostane k API.
    """
    if file_path.suffix.lower() != ".xml":
        return file_path, False

    tmp = Path(tempfile.gettempdir()) / f"{file_path.stem}.xml.gz"
    with open(file_path, "rb") as src, gzip.open(tmp, "wb", compresslevel=6) as dst:
        shutil.copyfileobj(src, dst, length=1024 * 1024)

    before = file_path.stat().st_size
    after = tmp.stat().st_size
    logger.info(
        "🗜️  Skomprimované: %.1f MB → %.1f MB (%.1f×)",
        before / 1024 / 1024, after / 1024 / 1024, before / max(after, 1),
    )
    return tmp, True


class HealthFileHandler(FileSystemEventHandler):
    """Spracúva udalosti vytvorenia súborov v iCloud priečinku"""

    def __init__(self):
        self.processed_files = set()
        self.load_processed_files()

    def load_processed_files(self):
        """Načítaj zoznam už spracovaných súborov"""
        if PROCESSED_LOG.exists():
            self.processed_files = {
                line.strip() for line in PROCESSED_LOG.read_text(encoding='utf-8').splitlines()
                if line.strip()
            }

    def save_processed_file(self, filename):
        """Ulož spracovaný súbor do logu"""
        with open(PROCESSED_LOG, 'a', encoding='utf-8') as f:
            f.write(f"{filename}\n")
        self.processed_files.add(filename)

    def on_created(self, event):
        """Udalosť: Nový súbor vytvorený"""
        if event.is_directory:
            return

        file_path = Path(event.src_path)

        if file_path.suffix.lower() not in WATCHED_SUFFIXES:
            return

        if file_path.name in self.processed_files:
            logger.info("⏭️  Skipping already processed: %s", file_path.name)
            return

        logger.info("📥 New file detected: %s", file_path.name)

        if not _wait_until_stable(file_path):
            logger.error("❌ %s nie je celý — preskakujem", file_path.name)
            return

        self.import_health_file(file_path)

    def import_health_file(self, file_path: Path):
        """Importuje Health súbor cez backend API"""
        payload = None
        compressed = False
        try:
            logger.info(
                "📊 Importing %s (%.2f MB)...",
                file_path.name, file_path.stat().st_size / 1024 / 1024,
            )

            payload, compressed = _compress(file_path)

            with open(payload, 'rb') as f:
                response = requests.post(
                    f"{BACKEND_URL}/api/apple-health/import-async",
                    files={'file': (payload.name, f, 'application/octet-stream')},
                    headers=_headers(),
                    timeout=600,
                )

            if response.status_code == 403:
                logger.error(
                    "❌ 403 Forbidden — backend vyžaduje X-Proxy-Secret. "
                    "Nastav MEDICALAI_PROXY_SECRET na tú istú hodnotu ako "
                    "PROXY_SHARED_SECRET na Railway."
                )
                return

            if response.status_code != 202:
                logger.error("❌ Import failed: %s", response.status_code)
                logger.error("   %s", response.text[:500])
                return

            job_id = response.json()["job_id"]
            logger.info("⏳ Job %s zaradený, čakám na dokončenie...", job_id)

            if self._await_job(job_id):
                self.save_processed_file(file_path.name)

        except Exception as e:
            logger.error("❌ Error importing %s: %s", file_path.name, e)
        finally:
            # Zmaž len to, čo sme vyrobili my — nikdy nie pôvodný export.
            if compressed and payload is not None:
                payload.unlink(missing_ok=True)

    def _await_job(self, job_id: str) -> bool:
        """Pollovanie stavu, kým import nedobehne. True = úspech."""
        deadline = time.time() + POLL_TIMEOUT

        while time.time() < deadline:
            time.sleep(POLL_INTERVAL)
            try:
                r = requests.get(
                    f"{BACKEND_URL}/api/apple-health/import-status/{job_id}",
                    headers=_headers(), timeout=30,
                )
            except requests.RequestException as e:
                logger.warning("⚠️  Nedostupný status jobu (skúsim znova): %s", e)
                continue

            if r.status_code != 200:
                logger.error("❌ Status %s: %s", r.status_code, r.text[:300])
                return False

            job = r.json()
            status = job.get("status")
            counts = job.get("counts") or {}

            if status == "completed":
                logger.info("✅ Import successful!")
                logger.info(
                    "   📈 Saved: %s, duplicates: %s, skipped: %s",
                    counts.get("saved", 0),
                    counts.get("duplicates", 0),
                    counts.get("skipped", 0),
                )
                return True

            if status == "failed":
                logger.error("❌ Import failed: %s", job.get("error", "neznáma chyba"))
                return False

            logger.info("   … %s (saved: %s)", status, counts.get("saved", 0))

        logger.error("❌ Job %s nedobehol do %ds", job_id, POLL_TIMEOUT)
        return False


def ensure_icloud_folder():
    """Vytvor iCloud priečinok, ak neexistuje"""
    if not ICLOUD_FOLDER.exists():
        logger.warning("⚠️  iCloud folder not found: %s", ICLOUD_FOLDER)
        logger.info("📁 Creating folder...")
        ICLOUD_FOLDER.mkdir(parents=True, exist_ok=True)
        logger.info("✅ Folder created: %s", ICLOUD_FOLDER)

        readme = ICLOUD_FOLDER / "README.txt"
        readme.write_text(
            "MedicalAI - Apple Health Exports\n\n"
            "Uložte sem export.xml, export.xml.gz alebo export.zip z iPhone Health app.\n"
            "Backend ich automaticky naimportuje.\n\n"
            f"Vytvorené: {datetime.now()}\n",
            encoding='utf-8',
        )
    else:
        logger.info("✅ iCloud folder found: %s", ICLOUD_FOLDER)


def check_backend_running():
    """Skontroluj, či je backend dosiahnuteľný.

    Kontroluje /api/health/status, nie /docs — to je za shared secretom (a na
    produkcii má byť zavreté), takže by to hlásilo nedostupný backend aj vtedy,
    keď beží úplne v poriadku.
    """
    try:
        response = requests.get(
            f"{BACKEND_URL}/api/health/status", headers=_headers(), timeout=15,
        )
        if response.status_code == 200:
            logger.info("✅ Backend is reachable at %s", BACKEND_URL)
            return True
        if response.status_code == 403:
            logger.error(
                "❌ 403 Forbidden z %s — chýba alebo nesedí X-Proxy-Secret. "
                "Nastav MEDICALAI_PROXY_SECRET.", BACKEND_URL,
            )
            return False
        logger.error("❌ Backend odpovedal %s", response.status_code)
    except Exception as e:
        logger.error("❌ Backend nedostupný na %s: %s", BACKEND_URL, e)

    return False


def process_existing_files(handler: "HealthFileHandler"):
    """Spracuj existujúce súbory v priečinku (pri prvom spustení)"""
    for file_path in sorted(ICLOUD_FOLDER.iterdir()):
        if file_path.is_dir() or file_path.suffix.lower() not in WATCHED_SUFFIXES:
            continue
        if file_path.name in handler.processed_files:
            continue
        logger.info("📂 Found existing file: %s", file_path.name)
        handler.import_health_file(file_path)


def main():
    """Hlavná funkcia - spustí file watcher"""
    logger.info("=" * 60)
    logger.info("🚀 MedicalAI - Apple Health Auto Import")
    logger.info("=" * 60)
    logger.info("🎯 Backend: %s", BACKEND_URL)
    logger.info("🔑 X-Proxy-Secret: %s", "nastavený" if PROXY_SECRET else "nenastavený")

    ensure_icloud_folder()

    if not check_backend_running():
        logger.error("⛔ Exiting...")
        return

    # Jedna instancia na celý beh, aby si watcher pamätal, čo práve doimportoval.
    handler = HealthFileHandler()

    logger.info("🔍 Checking for existing files...")
    process_existing_files(handler)

    logger.info("👀 Watching folder: %s", ICLOUD_FOLDER)
    logger.info("Press Ctrl+C to stop")
    logger.info("-" * 60)

    observer = Observer()
    observer.schedule(handler, str(ICLOUD_FOLDER), recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(CHECK_INTERVAL)
    except KeyboardInterrupt:
        logger.info("⏹️  Stopping file watcher...")
        observer.stop()

    observer.join()
    logger.info("✅ File watcher stopped")


if __name__ == "__main__":
    main()
