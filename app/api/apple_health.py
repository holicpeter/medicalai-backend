"""
Apple Health API - Import dát z iPhone Health appky
Podporuje import z export.xml súboru
"""
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
import asyncio
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import List, Dict, Any, Optional
import uuid
from pathlib import Path

# A full Apple Health export runs to hundreds of MB; cap it rather than let an
# oversized upload fill the container's disk.
_MAX_UPLOAD_BYTES = 600 * 1024 * 1024
_UPLOAD_CHUNK_SIZE = 1024 * 1024

from ..database.models import AppleHealthData, get_session

router = APIRouter(prefix="/api/apple-health", tags=["apple_health"])


# Mapovanie Apple Health typov na ľudsky čitateľné názvy
APPLE_HEALTH_TYPE_MAPPING = {
    # Základné metriky
    "HKQuantityTypeIdentifierStepCount": "Kroky",
    "HKQuantityTypeIdentifierDistanceWalkingRunning": "Vzdialenosť (chôdza/beh)",
    "HKQuantityTypeIdentifierFlightsClimbed": "Schody",
    "HKQuantityTypeIdentifierActiveEnergyBurned": "Aktívne kalórie",
    "HKQuantityTypeIdentifierBasalEnergyBurned": "Bazálne kalórie",
    
    # Srdce
    "HKQuantityTypeIdentifierHeartRate": "Srdcový tep",
    "HKQuantityTypeIdentifierRestingHeartRate": "Kľudový tep",
    "HKQuantityTypeIdentifierWalkingHeartRateAverage": "Priemerný tep pri chôdzi",
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN": "Variabilita tepu (HRV)",
    
    # Telesné merania
    "HKQuantityTypeIdentifierHeight": "Výška",
    "HKQuantityTypeIdentifierBodyMass": "Hmotnosť",
    "HKQuantityTypeIdentifierBodyMassIndex": "BMI",
    "HKQuantityTypeIdentifierBodyFatPercentage": "Telesný tuk %",
    "HKQuantityTypeIdentifierLeanBodyMass": "Svalová hmota",
    
    # Spánok
    "HKCategoryTypeIdentifierSleepAnalysis": "Spánok",
    
    # Dýchanie
    "HKQuantityTypeIdentifierRespiratoryRate": "Dychová frekvencia",
    "HKQuantityTypeIdentifierVO2Max": "VO2 Max",
    
    # Kyslík v krvi
    "HKQuantityTypeIdentifierOxygenSaturation": "Saturácia kyslíka",
    
    # Tlak
    "HKQuantityTypeIdentifierBloodPressureSystolic": "Systolický tlak",
    "HKQuantityTypeIdentifierBloodPressureDiastolic": "Diastolický tlak",
    
    # Glukóza
    "HKQuantityTypeIdentifierBloodGlucose": "Glukóza v krvi",
    
    # Teplota
    "HKQuantityTypeIdentifierBodyTemperature": "Telesná teplota",
    
    # Ženy
    "HKCategoryTypeIdentifierMenstrualFlow": "Menštruácia",
    
    # Ostatné
    "HKQuantityTypeIdentifierDietaryWater": "Pitie vody",
}


def parse_apple_health_date(date_str: str) -> Optional[datetime]:
    """Parse Apple Health datetime format, e.g. "2023-12-13 15:30:45 +0100".

    Keeps local wall-clock time (timezone offset is dropped, not applied).
    Returns None if the date cannot be parsed — never a fabricated value.
    """
    date_str = date_str.strip()
    try:
        return datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S %z").replace(tzinfo=None)
    except ValueError:
        pass
    try:
        # Fallback: first 19 chars are "YYYY-MM-DD HH:MM:SS"
        return datetime.strptime(date_str[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError as e:
        print(f"[APPLE HEALTH] Error parsing date '{date_str}': {e}")
        return None


def parse_apple_health_xml(xml_source, on_record=None) -> Dict[str, Any]:
    """
    Parse Apple Health export.xml súbor (iteratívne, streamované zo súboru).

    xml_source: cesta k súboru (str/Path) alebo bytes.
    on_record:  voliteľný callback volaný pre každý záznam. Ak je zadaný,
                záznamy sa nehromadia v pamäti — nutné pre veľké exporty.

    Returns:
        Dict s parsed dátami a štatistikami
    """
    try:
        import io
        from xml.etree.ElementTree import ParseError

        records = []
        stats = {
            "total_records": 0,
            "by_type": {},
            "date_range": {"start": None, "end": None}
        }

        print(f"[APPLE HEALTH] Using iterative XML parsing for better performance...")

        # Parse straight off disk when given a path — a full Apple Health export
        # is hundreds of MB and must never be held in memory as one blob.
        source = io.BytesIO(xml_source) if isinstance(xml_source, bytes) else str(xml_source)

        try:
            context = ET.iterparse(source, events=('start', 'end'))
            # Keep the root so processed children can be dropped as we go —
            # elem.clear() alone leaves them attached and the tree still grows.
            _, root = next(context)
        except ParseError as e:
            print(f"[APPLE HEALTH] Warning: XML parse error at line {e.position[0]}, trying alternative approach...")
            raise Exception(f"XML súbor má chybnú štruktúru na riadku {e.position[0]}. Skúste re-exportovať súbor z iPhone.")

        record_count = 0
        
        # Získať všetky <Record> elementy iteratívne
        for event, elem in context:
            if event != 'end' or elem.tag != 'Record':
                continue

            record = elem  # elem je už Record element
            record_type = record.get('type', '')
            value = record.get('value')
            unit = record.get('unit', '')
            start_date_str = record.get('startDate', '')
            end_date_str = record.get('endDate', '')
            creation_date_str = record.get('creationDate', '')
            source_name = record.get('sourceName', '')
            source_version = record.get('sourceVersion', '')
            
            # Device info (ak existuje)
            device = record.get('device', '')
            device_parts = {}
            if device:
                # Format: "<<HKDevice: ...>, name:iPhone, manufacturer:Apple, model:iPhone, hardware:iPhone14,2, software:16.6>"
                if 'name:' in device:
                    try:
                        device_parts['name'] = device.split('name:')[1].split(',')[0].strip()
                        device_parts['manufacturer'] = device.split('manufacturer:')[1].split(',')[0].strip()
                        device_parts['model'] = device.split('model:')[1].split(',')[0].strip()
                        device_parts['hardware'] = device.split('hardware:')[1].split(',')[0].strip()
                        device_parts['software'] = device.split('software:')[1].split('>')[0].strip()
                    except:
                        pass
            
            # Konvertovať value na float (ak je to číslo)
            try:
                value_float = float(value) if value else None
            except:
                value_float = None
            
            # Parse dates
            start_date = parse_apple_health_date(start_date_str) if start_date_str else None
            end_date = parse_apple_health_date(end_date_str) if end_date_str else None
            creation_date = parse_apple_health_date(creation_date_str) if creation_date_str else None
            
            # Metadata
            metadata = {}
            for meta in record.findall('.//MetadataEntry'):
                key = meta.get('key', '')
                val = meta.get('value', '')
                metadata[key] = val
            
            record_data = {
                "type": record_type,
                "value": value_float,
                "unit": unit,
                "start_date": start_date,
                "end_date": end_date,
                "creation_date": creation_date,
                "source_name": source_name,
                "source_version": source_version,
                "device": device_parts,
                "metadata": metadata if metadata else None
            }
            # A real export holds millions of records. When a consumer is given,
            # hand each one straight over instead of building the whole list.
            if on_record is not None:
                on_record(record_data)
            else:
                records.append(record_data)

            # Stats
            stats["total_records"] += 1
            
            # Count by type
            friendly_name = APPLE_HEALTH_TYPE_MAPPING.get(record_type, record_type)
            if friendly_name not in stats["by_type"]:
                stats["by_type"][friendly_name] = 0
            stats["by_type"][friendly_name] += 1
            
            # Date range
            if start_date:
                if stats["date_range"]["start"] is None or start_date < stats["date_range"]["start"]:
                    stats["date_range"]["start"] = start_date
                if stats["date_range"]["end"] is None or start_date > stats["date_range"]["end"]:
                    stats["date_range"]["end"] = start_date
            
            # Progress logging každých 5000 záznamov
            record_count += 1
            if record_count % 5000 == 0:
                print(f"[APPLE HEALTH] Parsed {record_count:,} records...")
            
            # Uvoľniť pamäť - dôležité pre veľké súbory!
            elem.clear()
            root.clear()

        print(f"[APPLE HEALTH] Parsing complete: {stats['total_records']:,} records")

        return {
            "records": records,
            "stats": stats
        }
        
    except Exception as e:
        raise Exception(f"Chyba pri parsovaní Apple Health XML: {str(e)}")


def _stream_import(xml_path, batch_id: str):
    """Parse and insert in one streaming pass.

    Records are handed over one at a time and flushed in batches, so a
    multi-hundred-MB export never materialises as a list. Duplicates are
    checked against a set built from one query instead of one SELECT per
    record — the latter turned a large import into millions of table scans.
    """
    session = get_session()
    counts = {"saved": 0, "skipped": 0, "duplicates": 0}
    batch = []
    BATCH_SIZE = 5000

    seen = {
        row
        for row in session.query(
            AppleHealthData.record_type,
            AppleHealthData.start_date,
            AppleHealthData.value,
            AppleHealthData.unit,
        ).all()
    }
    print(f"[APPLE HEALTH] Loaded {len(seen):,} existing keys for duplicate check")

    def flush():
        if batch:
            session.bulk_save_objects(batch)
            session.commit()
            batch.clear()

    def handle(record):
        if record["value"] is None or record["start_date"] is None:
            counts["skipped"] += 1
            return

        key = (record["type"], record["start_date"], record["value"], record["unit"])
        if key in seen:
            counts["duplicates"] += 1
            return
        seen.add(key)

        device = record["device"] or {}
        batch.append(AppleHealthData(
            patient_id=1,
            record_type=record["type"],
            value=record["value"],
            unit=record["unit"],
            start_date=record["start_date"],
            end_date=record["end_date"],
            creation_date=record["creation_date"],
            source_name=record["source_name"],
            source_version=record["source_version"],
            device_name=device.get("name"),
            device_manufacturer=device.get("manufacturer"),
            device_model=device.get("model"),
            device_hardware=device.get("hardware"),
            device_software=device.get("software"),
            record_metadata=record["metadata"],
            import_batch_id=batch_id,
        ))
        counts["saved"] += 1

        if len(batch) >= BATCH_SIZE:
            flush()
            print(f"[APPLE HEALTH] Progress: {counts['saved']:,} records imported...")

    try:
        parsed = parse_apple_health_xml(xml_path, on_record=handle)
        flush()
        return parsed["stats"], counts
    finally:
        session.close()


@router.post("/import")
async def import_apple_health_data(file: UploadFile = File(...)):
    """
    Import Apple Health XML súboru (akýkoľvek .xml súbor)
    
    Ako exportovať dáta z iPhone:
    1. Otvorte Health app
    2. Kliknite na svoj profil (hore vpravo)
    3. Scroll dole na "Export All Health Data"
    4. Kliknite "Export"
    5. Uložte export.zip
    6. Rozbaľte ZIP → získate export.xml
    7. Nahrajte export.xml (alebo export_small.xml, alebo iný .xml súbor) sem
    """
    tmp_path = None
    try:
        # Skontrolovať typ súboru - akceptuje AKÝKOĽVEK .xml súbor
        if not file.filename.lower().endswith('.xml'):
            raise HTTPException(
                status_code=400,
                detail="Neplatný súbor. Musí mať príponu .xml (napr. export.xml, export_small.xml)"
            )

        # export.zip contains two XML files. export_cda.xml is a Clinical Document
        # Architecture file with no <Record> elements, so importing it always
        # yields nothing — say so instead of reporting a successful empty import.
        if 'cda' in Path(file.filename).stem.lower():
            raise HTTPException(
                status_code=400,
                detail=(
                    "Toto je súbor export_cda.xml, ktorý obsahuje klinické dokumenty "
                    "v inom formáte a žiadne merania. Nahrajte prosím export.xml — "
                    "nájdete ho v tom istom priečinku apple_health_export."
                ),
            )

        # Stream the upload to disk in chunks. A full export is hundreds of MB and
        # reading it into memory would exhaust the container.
        with tempfile.NamedTemporaryFile(delete=False, suffix='.xml') as tmp:
            tmp_path = Path(tmp.name)
            bytes_written = 0
            while chunk := await file.read(_UPLOAD_CHUNK_SIZE):
                bytes_written += len(chunk)
                if bytes_written > _MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"Súbor je príliš veľký (limit "
                            f"{_MAX_UPLOAD_BYTES // (1024 * 1024)} MB)."
                        ),
                    )
                tmp.write(chunk)

        print(f"[APPLE HEALTH] Received {bytes_written / (1024*1024):.1f} MB, parsing {file.filename}...")

        batch_id = str(uuid.uuid4())[:8]

        # Parsing and importing are CPU/DB bound — keep them off the event loop.
        stats, counts = await asyncio.to_thread(_stream_import, tmp_path, batch_id)

        saved_count = counts["saved"]
        skipped_count = counts["skipped"]
        duplicate_count = counts["duplicates"]
        total_records = stats["total_records"]

        print(f"[APPLE HEALTH] Found {total_records} records")

        if total_records == 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    "V súbore sa nenašli žiadne merania. Uistite sa, že nahrávate "
                    "export.xml z priečinka apple_health_export (nie export_cda.xml "
                    "ani iný XML súbor)."
                ),
            )
        
        print(f"[APPLE HEALTH] Import complete: {saved_count} saved, {skipped_count} skipped, {duplicate_count} duplicates")

        # Newly imported records must be visible in /trends straight away.
        from app.analysis.trend_analyzer import TrendAnalyzer
        TrendAnalyzer.invalidate_cache()

        return JSONResponse(content={
            "success": True,
            "message": f"Import úspešný! Importovaných {saved_count} nových záznamov, {duplicate_count} duplikátov preskočených.",
            "batch_id": batch_id,
            "stats": {
                "total_records": total_records,
                "saved": saved_count,
                "skipped": skipped_count,
                "duplicates": duplicate_count,
                "by_type": stats["by_type"],
                "date_range": {
                    "start": stats["date_range"]["start"].isoformat() if stats["date_range"]["start"] else None,
                    "end": stats["date_range"]["end"].isoformat() if stats["date_range"]["end"] else None
                }
            }
        })
        
    except HTTPException:
        # Deliberate 4xx (wrong file, too large, no records) must reach the client
        # as-is instead of being reported as a server error.
        raise
    except Exception as e:
        print(f"[APPLE HEALTH] Import error: {e}")
        raise HTTPException(status_code=500, detail=f"Chyba pri importe: {str(e)}")
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


@router.get("/stats")
async def get_apple_health_stats():
    """Získať štatistiky importovaných Apple Health dát"""
    try:
        session = get_session()
        
        # Total records
        total_records = session.query(AppleHealthData).count()
        
        # By type
        from sqlalchemy import func
        by_type = session.query(
            AppleHealthData.record_type,
            func.count(AppleHealthData.id).label('count')
        ).group_by(AppleHealthData.record_type).all()
        
        by_type_dict = {}
        for record_type, count in by_type:
            friendly_name = APPLE_HEALTH_TYPE_MAPPING.get(record_type, record_type)
            by_type_dict[friendly_name] = count
        
        # Date range
        date_range = session.query(
            func.min(AppleHealthData.start_date).label('start'),
            func.max(AppleHealthData.start_date).label('end')
        ).first()
        
        # Unique devices
        unique_devices = session.query(AppleHealthData.device_name).distinct().all()
        devices = [d[0] for d in unique_devices if d[0]]
        
        # Latest import
        latest_import = session.query(
            func.max(AppleHealthData.imported_at)
        ).scalar()
        
        session.close()
        
        return JSONResponse(content={
            "total_records": total_records,
            "by_type": by_type_dict,
            "date_range": {
                "start": date_range.start.isoformat() if date_range.start else None,
                "end": date_range.end.isoformat() if date_range.end else None
            },
            "devices": devices,
            "latest_import": latest_import.isoformat() if latest_import else None
        })
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chyba: {str(e)}")


@router.get("/data/{record_type}")
async def get_apple_health_data_by_type(
    record_type: str,
    start_date: str = None,
    end_date: str = None,
    limit: int = 100
):
    """
    Získať Apple Health dáta podľa typu
    
    Príklady record_type:
    - HKQuantityTypeIdentifierStepCount (kroky)
    - HKQuantityTypeIdentifierHeartRate (srdcový tep)
    - HKQuantityTypeIdentifierBodyMass (hmotnosť)
    """
    try:
        session = get_session()
        
        query = session.query(AppleHealthData).filter(
            AppleHealthData.record_type == record_type
        )
        
        # Date filter
        if start_date:
            start_dt = datetime.fromisoformat(start_date)
            query = query.filter(AppleHealthData.start_date >= start_dt)
        
        if end_date:
            end_dt = datetime.fromisoformat(end_date)
            query = query.filter(AppleHealthData.start_date <= end_dt)
        
        # Order by date desc, limit
        query = query.order_by(AppleHealthData.start_date.desc()).limit(limit)
        
        records = query.all()
        
        session.close()
        
        result = []
        for record in records:
            result.append({
                "id": record.id,
                "type": record.record_type,
                "friendly_name": APPLE_HEALTH_TYPE_MAPPING.get(record.record_type, record.record_type),
                "value": record.value,
                "unit": record.unit,
                "start_date": record.start_date.isoformat() if record.start_date else None,
                "end_date": record.end_date.isoformat() if record.end_date else None,
                "source": record.source_name,
                "device": record.device_name
            })
        
        return JSONResponse(content={
            "record_type": record_type,
            "friendly_name": APPLE_HEALTH_TYPE_MAPPING.get(record_type, record_type),
            "count": len(result),
            "data": result
        })
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chyba: {str(e)}")


@router.delete("/data")
async def delete_all_apple_health_data():
    """Vymazať všetky Apple Health dáta z databázy"""
    try:
        session = get_session()
        
        count = session.query(AppleHealthData).count()
        session.query(AppleHealthData).delete()
        session.commit()
        session.close()
        
        return JSONResponse(content={
            "success": True,
            "message": f"Vymazaných {count} záznamov"
        })
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chyba: {str(e)}")


@router.get("/types")
async def get_available_types():
    """Získať zoznam všetkých dostupných typov záznamov v databáze"""
    try:
        session = get_session()
        
        types = session.query(AppleHealthData.record_type).distinct().all()
        
        session.close()
        
        result = []
        for (record_type,) in types:
            result.append({
                "id": record_type,
                "name": APPLE_HEALTH_TYPE_MAPPING.get(record_type, record_type)
            })
        
        # Sort by friendly name
        result.sort(key=lambda x: x["name"])
        
        return JSONResponse(content={"types": result})
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chyba: {str(e)}")


@router.get("/sport-stats")
async def get_sport_statistics():
    """Získať agregované športové štatistiky pre dashboard"""
    try:
        from sqlalchemy import func
        from datetime import timedelta
        import pandas as pd
        
        session = get_session()
        now = datetime.now()
        
        # Helper funkcia pre agregáciu
        def aggregate_daily(record_type: str, days: int = 7):
            """Agreguje denné hodnoty pre daný typ metriky"""
            start_date = now - timedelta(days=days)
            records = session.query(AppleHealthData).filter(
                AppleHealthData.record_type == record_type,
                AppleHealthData.start_date >= start_date
            ).all()
            
            if not records:
                return []
            
            df = pd.DataFrame([{
                'date': r.start_date.date(),
                'value': r.value
            } for r in records])
            
            # Group by date and sum/avg
            daily = df.groupby('date')['value'].agg(['sum', 'mean', 'max', 'min']).reset_index()
            daily['date'] = daily['date'].astype(str)
            return daily.to_dict('records')
        
        # KROKY
        steps_data = aggregate_daily('HKQuantityTypeIdentifierStepCount', 30)
        steps_today = steps_data[-1]['sum'] if steps_data else 0
        steps_7d = sum(d['sum'] for d in steps_data[-7:]) / 7 if len(steps_data) >= 7 else 0
        steps_30d = sum(d['sum'] for d in steps_data) / len(steps_data) if steps_data else 0
        steps_trend = 'up' if steps_7d > steps_30d else 'down' if steps_7d < steps_30d * 0.9 else 'stable'
        
        # SRDCOVÝ TEP
        hr_data = aggregate_daily('HKQuantityTypeIdentifierHeartRate', 7)
        hr_current = hr_data[-1]['mean'] if hr_data else 0
        hr_max = hr_data[-1]['max'] if hr_data else 0
        hr_7d = sum(d['mean'] for d in hr_data) / len(hr_data) if hr_data else 0
        
        resting_hr_data = aggregate_daily('HKQuantityTypeIdentifierRestingHeartRate', 7)
        resting_hr = resting_hr_data[-1]['mean'] if resting_hr_data else 0
        
        # SPÁNOK (v hodinách)
        sleep_records = session.query(AppleHealthData).filter(
            AppleHealthData.record_type == 'HKCategoryTypeIdentifierSleepAnalysis',
            AppleHealthData.start_date >= now - timedelta(days=7)
        ).all()
        
        sleep_by_day = {}
        for r in sleep_records:
            day = r.start_date.date()
            if day not in sleep_by_day:
                sleep_by_day[day] = 0
            # Vypočítame dĺžku spánku v hodinách
            if r.end_date and r.start_date:
                duration_hours = (r.end_date - r.start_date).total_seconds() / 3600
                sleep_by_day[day] += duration_hours
        
        sleep_history = [{'date': str(d), 'hours': h} for d, h in sorted(sleep_by_day.items())]
        sleep_last = sleep_history[-1]['hours'] if sleep_history else 0
        sleep_7d = sum(d['hours'] for d in sleep_history) / len(sleep_history) if sleep_history else 0
        sleep_30d = sleep_7d  # Simplified
        sleep_quality = 'good' if sleep_last >= 7 else 'fair' if sleep_last >= 6 else 'poor'
        
        # AKTIVITA & KALÓRIE
        calories_data = aggregate_daily('HKQuantityTypeIdentifierActiveEnergyBurned', 7)
        calories_today = calories_data[-1]['sum'] if calories_data else 0
        
        distance_data = aggregate_daily('HKQuantityTypeIdentifierDistanceWalkingRunning', 7)
        distance_today = distance_data[-1]['sum'] / 1000 if distance_data else 0  # convert m to km
        
        # Spojíme kalórie a aktívne minúty (estimované z krokov/vzdialenosti)
        activity_history = []
        for i, cal in enumerate(calories_data):
            activity_history.append({
                'date': cal['date'],
                'calories': int(cal['sum']),
                'minutes': int(cal['sum'] / 5) if cal['sum'] else 0  # Estimate: ~5 cal/min
            })
        
        # HMOTNOSŤ
        weight_data = aggregate_daily('HKQuantityTypeIdentifierBodyMass', 30)
        weight_current = weight_data[-1]['mean'] if weight_data else 0
        weight_7d_ago = weight_data[-7]['mean'] if len(weight_data) >= 7 else weight_current
        weight_trend = weight_current - weight_7d_ago
        
        bmi_data = aggregate_daily('HKQuantityTypeIdentifierBodyMassIndex', 30)
        bmi_current = bmi_data[-1]['mean'] if bmi_data else 0
        
        weight_history = []
        for w in weight_data:
            entry = {'date': w['date'], 'weight': round(w['mean'], 1)}
            # Try to find matching BMI
            matching_bmi = next((b for b in bmi_data if b['date'] == w['date']), None)
            if matching_bmi:
                entry['bmi'] = round(matching_bmi['mean'], 1)
            weight_history.append(entry)
        
        session.close()
        
        return JSONResponse(content={
            "steps": {
                "today": int(steps_today),
                "avg_7d": int(steps_7d),
                "avg_30d": int(steps_30d),
                "trend": steps_trend,
                "history": [{'date': d['date'], 'value': int(d['sum'])} for d in steps_data]
            },
            "heart_rate": {
                "current": int(hr_current),
                "resting": int(resting_hr),
                "max": int(hr_max),
                "avg_7d": int(hr_7d),
                "history": [{'date': d['date'], 'value': int(d['mean'])} for d in hr_data]
            },
            "sleep": {
                "last_night_hours": round(sleep_last, 1),
                "avg_7d": round(sleep_7d, 1),
                "avg_30d": round(sleep_30d, 1),
                "quality": sleep_quality,
                "history": sleep_history
            },
            "activity": {
                "calories_today": int(calories_today),
                "active_minutes_today": int(calories_today / 5) if calories_today else 0,
                "distance_km_today": round(distance_today, 1),
                "workouts_this_week": 0,  # TODO: Calculate from workout records
                "history": activity_history
            },
            "weight": {
                "current": round(weight_current, 1),
                "trend_7d": round(weight_trend, 1),
                "bmi": round(bmi_current, 1),
                "history": weight_history
            }
        })
        
    except Exception as e:
        print(f"[APPLE HEALTH SPORT STATS] Error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Chyba: {str(e)}")
