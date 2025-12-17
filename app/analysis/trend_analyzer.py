import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from pathlib import Path
import json

from app.config import settings
from app.database import get_session, HealthRecord, AppleHealthData


def _to_float(value):
    """Convert value to float, handling comma decimal and non-numeric gracefully."""
    if value is None:
        return None
    try:
        return float(str(value).replace(',', '.'))
    except Exception:
        return None

class TrendAnalyzer:
    """Analyzuje trendy v zdravotných ukazovateľoch"""
    
    # Class-level cache pre dáta (zdieľaný medzi inštanciami)
    _data_cache = None
    _cache_timestamp = None
    _cache_ttl = 300  # 5 minút cache
    
    def __init__(self):
        # Použiť cache ak je aktuálny
        if (TrendAnalyzer._data_cache is not None and 
            TrendAnalyzer._cache_timestamp and
            (datetime.now() - TrendAnalyzer._cache_timestamp).total_seconds() < TrendAnalyzer._cache_ttl):
            self.data = TrendAnalyzer._data_cache
            print(f"[TREND] Using cached data ({len(self.data)} rows)")
        else:
            self.data = self._load_data()
            TrendAnalyzer._data_cache = self.data
            TrendAnalyzer._cache_timestamp = datetime.now()
            print(f"[TREND] Loaded fresh data ({len(self.data)} rows), cached for {TrendAnalyzer._cache_ttl}s")
    
    def _load_data(self) -> pd.DataFrame:
        """Načíta všetky zdravotné dáta (OCR + Manuálne + Apple Health)"""
        all_metrics = []
        
        # 1. Načítať OCR extrahované dáta zo súborov
        for json_file in settings.PROCESSED_DATA_DIR.glob("extracted_data_*.json"):
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    metrics = json.load(f)
                    # Normalizovať názvy metrík (pulse → heart_rate)
                    for metric in metrics:
                        if metric.get('metric') == 'pulse':
                            metric['metric'] = 'heart_rate'
                    all_metrics.extend(metrics)
            except Exception as e:
                print(f"[TREND] Error loading {json_file}: {e}")
        
        # 2. Načítať manuálne zadané záznamy z databázy
        try:
            session = get_session()
            manual_records = session.query(HealthRecord).filter_by(source="manual").all()
            
            print(f"[TREND] Found {len(manual_records)} manual records")
            
            for record in manual_records:
                # Normalizovať názvy metrík (pulse → heart_rate)
                metric_type = record.metric_type
                if metric_type == 'pulse':
                    metric_type = 'heart_rate'
                    
                metric_data = {
                    'date': record.record_date,  # Nechať ako date objekt, nie .isoformat()
                    'metric': metric_type,
                    'value': _to_float(record.value),
                    'unit': record.unit,
                    'source': 'manual',
                    'notes': record.notes
                }
                print(f"[TREND] Manual: {metric_type} = {record.value} {record.unit} on {record.record_date}")
                all_metrics.append(metric_data)
            
            session.close()
        except Exception as e:
            print(f"[TREND] Error loading manual records: {e}")
            import traceback
            traceback.print_exc()
        
        # 3. Načítať Apple Health dáta z databázy
        try:
            session = get_session()
            
            print("[TREND] Loading Apple Health records...")
            
            # Mapovanie Apple Health typov na naše metriky
            apple_to_metric_map = {
                'HKQuantityTypeIdentifierBodyMass': 'weight',
                'HKQuantityTypeIdentifierHeight': 'height',
                'HKQuantityTypeIdentifierHeartRate': 'heart_rate',
                'HKQuantityTypeIdentifierBloodPressureSystolic': 'blood_pressure_systolic',
                'HKQuantityTypeIdentifierBloodPressureDiastolic': 'blood_pressure_diastolic',
                'HKQuantityTypeIdentifierBodyMassIndex': 'bmi',
                'HKQuantityTypeIdentifierBloodGlucose': 'glucose',
            }
            
            # Načítať len relevantné typy (nie všetky 643k záznamov)
            relevant_types = list(apple_to_metric_map.keys())
            apple_records = session.query(AppleHealthData).filter(
                AppleHealthData.record_type.in_(relevant_types)
            ).all()
            
            print(f"[TREND] Found {len(apple_records)} Apple Health records (filtered by type)")
            
            for record in apple_records:
                metric_name = apple_to_metric_map.get(record.record_type)
                if metric_name and record.value is not None:
                    all_metrics.append({
                        'date': record.start_date,  # Nechať ako datetime objekt, nie .isoformat()
                        'metric': metric_name,
                        'value': float(record.value),
                        'unit': record.unit,
                        'source': 'apple_health',
                        'device': record.device_name
                    })
            
            print(f"[TREND] Converted {len([m for m in all_metrics if m.get('source') == 'apple_health'])} Apple Health records to metrics")
            
            session.close()
        except Exception as e:
            print(f"[TREND] Error loading Apple Health records: {e}")
            import traceback
            traceback.print_exc()
        
        if not all_metrics:
            return pd.DataFrame()
        
        print(f"[TREND] Total metrics before DataFrame: {len(all_metrics)}")
        
        # Konvertovať na DataFrame
        df = pd.DataFrame(all_metrics)
        
        print(f"[TREND] DataFrame shape before conversion: {df.shape}")
        print(f"[TREND] DataFrame columns: {df.columns.tolist()}")
        print(f"[TREND] Sample data:\n{df.head(3)}")
        
        # Konvertovať dátum
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'], errors='coerce')
            print(f"[TREND] Rows after date conversion: {len(df)}, NaN dates: {df['date'].isna().sum()}")
        
        # Konvertovať hodnotu na číslo
        if 'value' in df.columns:
            df['value'] = pd.to_numeric(df['value'], errors='coerce')
            print(f"[TREND] Rows after value conversion: {len(df)}, NaN values: {df['value'].isna().sum()}")
        
        # Odstrániť riadky bez dátumu alebo hodnoty
        rows_before = len(df)
        df = df.dropna(subset=['date', 'value'])
        rows_after = len(df)
        print(f"[TREND] Dropped {rows_before - rows_after} rows with NaN date/value")
        
        # Zoradiť podľa dátumu
        df = df.sort_values('date')
        
        print(f"[TREND] Loaded {len(df)} total metrics from all sources")
        
        return df
    
    def analyze_trends(
        self,
        metric: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Dict:
        """
        Analyzuje trendy v zdravotných ukazovateľoch
        
        Args:
            metric: Konkrétna metrika alebo None pre všetky
            start_date: Začiatočný dátum (YYYY-MM-DD)
            end_date: Konečný dátum (YYYY-MM-DD)
        """
        print(f"[TREND] analyze_trends called: metric={metric}, start={start_date}, end={end_date}")
        print(f"[TREND] Data shape: {self.data.shape if not self.data.empty else 'EMPTY'}")
        
        if self.data.empty:
            print("[TREND] WARNING: No data available!")
            return {"trends": {}, "message": "Zatiaľ nie sú k dispozícii žiadne dáta. Pridajte zdravotné záznamy manuálne alebo nahrajte dokumenty."}
        
        df = self.data.copy()
        
        # Filtrovať podľa dátumu (len ak máme validné dátumy)
        if 'date' in df.columns:
            if start_date:
                try:
                    df = df[df['date'] >= pd.to_datetime(start_date)]
                except:
                    pass
            if end_date:
                try:
                    df = df[df['date'] <= pd.to_datetime(end_date)]
                except:
                    pass
        
        # Filtrovať podľa metriky
        if metric:
            df = df[df['metric'] == metric]
        
        if df.empty:
            return {"error": "No data found", "message": "Žiadne dáta pre zvolené filtre"}
        
        trends = {}
        
        # Analyzovať každú metriku
        for metric_name in df['metric'].unique():
            try:
                metric_data = df[df['metric'] == metric_name]
                trend = self._analyze_single_metric(metric_name, metric_data)
                trends[metric_name] = trend
            except Exception as e:
                print(f"[TREND ERROR] Cannot analyze {metric_name}: {e}")
                trends[metric_name] = {"error": str(e)}
        
        return trends
    
    def _analyze_single_metric(self, metric_name: str, data: pd.DataFrame) -> Dict:
        """Analyzuje trend jednej metriky"""
        if data.empty:
            return {"error": "No data for this metric"}
        
        try:
            # Špeciálne spracovanie pre krvný tlak
            if metric_name == 'blood_pressure':
                return self._analyze_blood_pressure(data)
            
            # Štandardná analýza
            values = data['value'].dropna()
            
            if len(values) == 0:
                return {"error": "No valid values"}
            
            # Konvertovať na float
            numeric_values = []
            for val in values:
                if isinstance(val, (int, float)):
                    numeric_values.append(float(val))
            
            if len(numeric_values) == 0:
                return {"error": "No numeric values"}
            
            trend_data = {
                'count': len(numeric_values),
                'latest': float(numeric_values[-1]) if len(numeric_values) > 0 else None,
                'mean': float(np.mean(numeric_values)),
                'min': float(np.min(numeric_values)),
                'max': float(np.max(numeric_values)),
                'std': float(np.std(numeric_values)) if len(numeric_values) > 1 else 0,
                'trend': self._calculate_trend(data),
                'values_over_time': self._get_values_over_time(data)
            }
            
            # Pridať interpretáciu
            trend_data['interpretation'] = self._interpret_metric(metric_name, trend_data)
            
            return trend_data
            
        except Exception as e:
            print(f"[TREND ERROR] Cannot analyze {metric_name}: {e}")
            import traceback
            traceback.print_exc()
            return {"error": str(e)}
    
    def _analyze_blood_pressure(self, data: pd.DataFrame) -> Dict:
        """Špeciálna analýza pre krvný tlak"""
        systolic_values = []
        diastolic_values = []
        
        for _, row in data.iterrows():
            if isinstance(row['value'], dict):
                systolic_values.append(row['value'].get('systolic'))
                diastolic_values.append(row['value'].get('diastolic'))
        
        return {
            'systolic': {
                'latest': systolic_values[-1] if systolic_values else None,
                'mean': np.mean(systolic_values) if systolic_values else None,
                'min': np.min(systolic_values) if systolic_values else None,
                'max': np.max(systolic_values) if systolic_values else None,
            },
            'diastolic': {
                'latest': diastolic_values[-1] if diastolic_values else None,
                'mean': np.mean(diastolic_values) if diastolic_values else None,
                'min': np.min(diastolic_values) if diastolic_values else None,
                'max': np.max(diastolic_values) if diastolic_values else None,
            },
            'interpretation': self._interpret_blood_pressure(systolic_values, diastolic_values)
        }
    
    def _calculate_trend(self, data: pd.DataFrame) -> str:
        """Vypočíta trend (vzostupný/zostupný/stabilný)"""
        if len(data) < 2:
            return "insufficient_data"
        
        # Filtrovať len riadky s validným dátumom
        if 'date' not in data.columns:
            return "no_date_data"
        
        data_with_dates = data.dropna(subset=['date']).copy()
        
        if len(data_with_dates) < 2:
            return "insufficient_data"
        
        # Lineárna regresia
        data_sorted = data_with_dates.sort_values('date')
        
        # Konvertovať hodnoty na float, ignorovať dict (blood_pressure)
        values = []
        for val in data_sorted['value'].values:
            if isinstance(val, (int, float)):
                values.append(float(val))
            elif isinstance(val, dict):
                # Pre blood pressure použiť systolic
                if 'systolic' in val:
                    values.append(float(val['systolic']))
        
        if len(values) < 2:
            return "insufficient_data"
        
        # Konvertovať na numpy array s explicitným float64
        values_array = np.array(values, dtype=np.float64)
        x = np.arange(len(values_array), dtype=np.float64)
        
        try:
            slope = np.polyfit(x, values_array, 1)[0]
            
            if slope > 0.1:
                return "increasing"
            elif slope < -0.1:
                return "decreasing"
            else:
                return "stable"
        except Exception as e:
            print(f"[TREND ERROR] Cannot calculate trend: {e}")
            return "unknown"
    
    def _get_values_over_time(self, data: pd.DataFrame) -> List[Dict]:
        """Získa hodnoty v čase pre grafické zobrazenie"""
        if 'date' not in data.columns:
            return []
        
        # Len riadky s validným dátumom
        data_with_dates = data.dropna(subset=['date']).copy()
        
        if data_with_dates.empty:
            return []
        
        data_sorted = data_with_dates.sort_values('date')
        values_list = []
        
        for _, row in data_sorted.iterrows():
            val = row['value']
            
            # Konvertovať dict (blood_pressure) na číslo
            if isinstance(val, dict):
                val = val.get('systolic', None)
            
            try:
                date_str = row['date'].strftime('%Y-%m-%d') if pd.notna(row['date']) else None
                value_float = float(val) if val is not None and not isinstance(val, dict) else None
                
                if date_str and value_float is not None:
                    values_list.append({
                        'date': date_str,
                        'value': value_float
                    })
            except Exception as e:
                print(f"[TREND ERROR] Cannot process value: {e}")
                continue
        
        return values_list
    
    def _interpret_metric(self, metric_name: str, trend_data: Dict) -> str:
        """Interpretuje metriku (v norme, zvýšená, znížená)"""
        latest = trend_data.get('latest')
        if latest is None:
            return "no_data"
        
        # Referenčné hodnoty
        normal_ranges = {
            'glucose': (3.9, 5.6),  # mmol/L nalačno
            'hba1c': (0, 5.7),  # %
            'cholesterol': (0, 5.2),  # mmol/L
            'ldl': (0, 3.0),  # mmol/L
            'hdl': (1.0, float('inf')),  # mmol/L (vyššie je lepšie)
            'triglycerides': (0, 1.7),  # mmol/L
            'bmi': (18.5, 24.9),
        }
        
        if metric_name in normal_ranges:
            min_val, max_val = normal_ranges[metric_name]
            if latest < min_val:
                return "below_normal"
            elif latest > max_val:
                return "above_normal"
            else:
                return "normal"
        
        return "unknown"
    
    def _interpret_blood_pressure(self, systolic: List, diastolic: List) -> str:
        """Interpretuje krvný tlak"""
        if not systolic or not diastolic:
            return "no_data"
        
        latest_sys = systolic[-1]
        latest_dia = diastolic[-1]
        
        if latest_sys < 120 and latest_dia < 80:
            return "normal"
        elif latest_sys < 130 and latest_dia < 80:
            return "elevated"
        elif latest_sys < 140 or latest_dia < 90:
            return "hypertension_stage_1"
        elif latest_sys < 180 or latest_dia < 120:
            return "hypertension_stage_2"
        else:
            return "hypertension_crisis"
    
    def get_summary(self, trends: Dict) -> Dict:
        """Vytvorí súhrn trendov"""
        summary = {
            'total_metrics': len(trends),
            'metrics_analyzed': list(trends.keys()),
            'concerning_trends': [],
            'positive_trends': []
        }
        
        for metric, data in trends.items():
            interpretation = data.get('interpretation', '')
            
            if 'above_normal' in str(interpretation) or 'hypertension' in str(interpretation):
                summary['concerning_trends'].append(metric)
            elif interpretation == 'normal' or interpretation == 'improving':
                summary['positive_trends'].append(metric)
        
        return summary
