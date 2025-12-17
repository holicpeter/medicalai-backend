"""
Apple Health Auto Import - File Watcher
Sleduje iCloud priečinok a automaticky importuje nové Health dáta
"""

import time
import logging
from pathlib import Path
from datetime import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import requests

# Nastavenie
ICLOUD_FOLDER = Path.home() / "iCloudDrive" / "MedicalAI" / "exports"
BACKEND_URL = "http://localhost:8000"
CHECK_INTERVAL = 60  # sekúnd

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('apple_health_sync.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class HealthFileHandler(FileSystemEventHandler):
    """Spracúva udalosti vytvorenia súborov v iCloud priečinku"""
    
    def __init__(self):
        self.processed_files = set()
        self.load_processed_files()
    
    def load_processed_files(self):
        """Načítaj zoznam už spracovaných súborov"""
        processed_log = Path("processed_files.txt")
        if processed_log.exists():
            with open(processed_log, 'r') as f:
                self.processed_files = set(line.strip() for line in f)
    
    def save_processed_file(self, filename):
        """Ulož spracovaný súbor do logu"""
        with open("processed_files.txt", 'a') as f:
            f.write(f"{filename}\n")
        self.processed_files.add(filename)
    
    def on_created(self, event):
        """Udalosť: Nový súbor vytvorený"""
        if event.is_directory:
            return
        
        file_path = Path(event.src_path)
        
        # Spracuj len XML alebo CSV súbory
        if file_path.suffix.lower() not in ['.xml', '.csv']:
            return
        
        # Preskočiť, ak už bol spracovaný
        if file_path.name in self.processed_files:
            logger.info(f"⏭️  Skipping already processed: {file_path.name}")
            return
        
        logger.info(f"📥 New file detected: {file_path.name}")
        
        # Počkaj, kým sa súbor úplne stiahne (iCloud môže stiahnuť postupne)
        time.sleep(5)
        
        # Import súboru
        self.import_health_file(file_path)
    
    def import_health_file(self, file_path: Path):
        """Importuje Health súbor cez backend API"""
        try:
            file_size_mb = file_path.stat().st_size / 1024 / 1024
            logger.info(f"📊 Importing {file_path.name} ({file_size_mb:.2f} MB)...")
            
            # Upload cez API
            with open(file_path, 'rb') as f:
                files = {'file': (file_path.name, f, 'application/xml')}
                
                response = requests.post(
                    f"{BACKEND_URL}/api/apple-health/import",
                    files=files,
                    timeout=600  # 10 minút timeout pre veľké súbory
                )
            
            if response.status_code == 200:
                result = response.json()
                logger.info(f"✅ Import successful!")
                logger.info(f"   📈 Records imported: {result.get('total_records', 'N/A')}")
                logger.info(f"   ⏱️  Time taken: {result.get('time_taken', 'N/A')}")
                
                # Označ ako spracovaný
                self.save_processed_file(file_path.name)
                
                # Voliteľne: Vymaž súbor po importe (ušetrí miesto v iCloud)
                # file_path.unlink()
                # logger.info(f"🗑️  Deleted: {file_path.name}")
                
            else:
                logger.error(f"❌ Import failed: {response.status_code}")
                logger.error(f"   {response.text}")
        
        except Exception as e:
            logger.error(f"❌ Error importing {file_path.name}: {e}")


def ensure_icloud_folder():
    """Vytvor iCloud priečinok, ak neexistuje"""
    if not ICLOUD_FOLDER.exists():
        logger.warning(f"⚠️  iCloud folder not found: {ICLOUD_FOLDER}")
        logger.info(f"📁 Creating folder...")
        ICLOUD_FOLDER.mkdir(parents=True, exist_ok=True)
        logger.info(f"✅ Folder created: {ICLOUD_FOLDER}")
        
        # Vytvor README
        readme = ICLOUD_FOLDER / "README.txt"
        readme.write_text(
            "MedicalAI - Apple Health Exports\n\n"
            "Uložte sem export.xml súbory z iPhone Health app.\n"
            "Backend ich automaticky naimportuje.\n\n"
            f"Vytvorené: {datetime.now()}\n"
        )
    else:
        logger.info(f"✅ iCloud folder found: {ICLOUD_FOLDER}")


def check_backend_running():
    """Skontroluj, či backend beží"""
    try:
        response = requests.get(f"{BACKEND_URL}/docs", timeout=5)
        if response.status_code == 200:
            logger.info(f"✅ Backend is running at {BACKEND_URL}")
            return True
    except Exception:
        pass
    
    logger.error(f"❌ Backend is NOT running at {BACKEND_URL}")
    logger.error(f"   Please start backend first:")
    logger.error(f"   cd backend && uvicorn app.main:app --reload")
    return False


def process_existing_files():
    """Spracuj existujúce súbory v priečinku (pri prvom spustení)"""
    handler = HealthFileHandler()
    
    for file_path in ICLOUD_FOLDER.glob("*.xml"):
        if file_path.name not in handler.processed_files:
            logger.info(f"📂 Found existing file: {file_path.name}")
            handler.import_health_file(file_path)


def main():
    """Hlavná funkcia - spustí file watcher"""
    logger.info("=" * 60)
    logger.info("🚀 MedicalAI - Apple Health Auto Import")
    logger.info("=" * 60)
    
    # Kontroly
    ensure_icloud_folder()
    
    if not check_backend_running():
        logger.error("⛔ Exiting... Start backend first!")
        return
    
    # Spracuj existujúce súbory
    logger.info("🔍 Checking for existing files...")
    process_existing_files()
    
    # Spusti file watcher
    logger.info(f"👀 Watching folder: {ICLOUD_FOLDER}")
    logger.info(f"⏱️  Check interval: {CHECK_INTERVAL}s")
    logger.info("Press Ctrl+C to stop")
    logger.info("-" * 60)
    
    event_handler = HealthFileHandler()
    observer = Observer()
    observer.schedule(event_handler, str(ICLOUD_FOLDER), recursive=False)
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
