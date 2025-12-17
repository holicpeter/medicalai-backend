import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, Optional
from pathlib import Path
import json

from app.config import settings

class HealthMetricsAnalyzer:
    """Analyzuje aktuálne zdravotné metriky"""
    
    def __init__(self):
        self.data = self._load_all_data()
    
    def _load_all_data(self) -> pd.DataFrame:
        """Načíta všetky extrahované dáta"""
        all_metrics = []
        
        for json_file in settings.PROCESSED_DATA_DIR.glob("extracted_data_*.json"):
            with open(json_file, 'r', encoding='utf-8') as f:
                metrics = json.load(f)
                all_metrics.extend(metrics)
        
        if not all_metrics:
            return pd.DataFrame()
        
        df = pd.DataFrame(all_metrics)
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'], errors='coerce')
        
        return df
    
    def get_latest_metrics(self) -> Dict:
        """Získa najnovšie hodnoty všetkých metrík"""
        if self.data.empty:
            return {"error": "No data available"}
        
        latest_metrics = {}
        
        for metric_name in self.data['metric'].unique():
            metric_data = self.data[self.data['metric'] == metric_name]
            metric_data = metric_data.dropna(subset=['date'])
            
            if not metric_data.empty:
                latest_row = metric_data.sort_values('date').iloc[-1]
                latest_metrics[metric_name] = {
                    'value': latest_row['value'],
                    'date': latest_row['date'].strftime('%Y-%m-%d') if pd.notna(latest_row['date']) else None,
                    'status': self._get_metric_status(metric_name, latest_row['value'])
                }
        
        return latest_metrics
    
    def get_metrics_history(self, days: int = 365) -> Dict:
        """Získa históriu meraní za posledných N dní"""
        if self.data.empty:
            return {"error": "No data available"}
        
        cutoff_date = datetime.now() - timedelta(days=days)
        recent_data = self.data[self.data['date'] >= cutoff_date]
        
        history = {}
        
        for metric_name in recent_data['metric'].unique():
            metric_data = recent_data[recent_data['metric'] == metric_name]
            metric_data = metric_data.sort_values('date')
            
            history[metric_name] = [
                {
                    'date': row['date'].strftime('%Y-%m-%d') if pd.notna(row['date']) else None,
                    'value': row['value']
                }
                for _, row in metric_data.iterrows()
            ]
        
        return history
    
    def get_comprehensive_summary(self) -> Dict:
        """Komplexný zdravotný prehľad"""
        if self.data.empty:
            return {"error": "No data available"}
        
        latest = self.get_latest_metrics()
        
        summary = {
            'generated_at': datetime.now().isoformat(),
            'latest_metrics': latest,
            'health_score': self._calculate_health_score(latest),
            'alerts': self._generate_alerts(latest),
            'recommendations': self._generate_basic_recommendations(latest)
        }
        
        return summary
    
    def _get_metric_status(self, metric_name: str, value) -> str:
        """Určí status metriky (normal, warning, alert)"""
        if value is None:
            return "unknown"
        
        # Pre krvný tlak
        if metric_name == 'blood_pressure' and isinstance(value, dict):
            sys = value.get('systolic', 0)
            dia = value.get('diastolic', 0)
            if sys >= 140 or dia >= 90:
                return "alert"
            elif sys >= 130 or dia >= 80:
                return "warning"
            else:
                return "normal"
        
        # Pre ostatné metriky
        thresholds = {
            'glucose': {'warning': 5.6, 'alert': 7.0},
            'hba1c': {'warning': 5.7, 'alert': 6.5},
            'cholesterol': {'warning': 5.2, 'alert': 6.2},
            'ldl': {'warning': 3.0, 'alert': 4.0},
            'triglycerides': {'warning': 1.7, 'alert': 2.3},
            'bmi': {'warning': 25, 'alert': 30}
        }
        
        if metric_name in thresholds:
            if value >= thresholds[metric_name]['alert']:
                return "alert"
            elif value >= thresholds[metric_name]['warning']:
                return "warning"
            else:
                return "normal"
        
        return "normal"
    
    def _calculate_health_score(self, latest_metrics: Dict) -> int:
        """Vypočíta celkové zdravotné skóre (0-100)"""
        if not latest_metrics or 'error' in latest_metrics:
            return 0
        
        score = 100
        
        for metric_name, data in latest_metrics.items():
            status = data.get('status', 'normal')
            
            if status == 'alert':
                score -= 15
            elif status == 'warning':
                score -= 5
        
        return max(0, min(100, score))
    
    def _generate_alerts(self, latest_metrics: Dict) -> list:
        """Generuje upozornenia na základe aktuálnych metrík"""
        alerts = []
        
        if not latest_metrics or 'error' in latest_metrics:
            return alerts
        
        for metric_name, data in latest_metrics.items():
            status = data.get('status')
            value = data.get('value')
            
            if status == 'alert':
                alerts.append({
                    'severity': 'high',
                    'metric': metric_name,
                    'message': f'{metric_name} je výrazne nad normou',
                    'value': value,
                    'recommendation': f'Konzultujte s lekárom ohľadom {metric_name}'
                })
            elif status == 'warning':
                alerts.append({
                    'severity': 'medium',
                    'metric': metric_name,
                    'message': f'{metric_name} je mierne zvýšený',
                    'value': value,
                    'recommendation': f'Monitorujte {metric_name} a zvážte úpravu životného štýlu'
                })
        
        return alerts
    
    def _generate_basic_recommendations(self, latest_metrics: Dict) -> list:
        """Generuje základné odporúčania"""
        recommendations = []
        
        if not latest_metrics or 'error' in latest_metrics:
            return recommendations
        
        # Všeobecné odporúčania
        recommendations.append({
            'category': 'general',
            'title': 'Pravidelné kontroly',
            'description': 'Odporúčame pravidelnú kontrolu zdravotného stavu'
        })
        
        # Špecifické odporúčania na základe metrík
        if 'glucose' in latest_metrics or 'hba1c' in latest_metrics:
            recommendations.append({
                'category': 'diabetes_prevention',
                'title': 'Kontrola glykémie',
                'description': 'Monitorujte hladiny cukru a zvážte konzultáciu s diabetológom'
            })
        
        if 'blood_pressure' in latest_metrics:
            recommendations.append({
                'category': 'cardiovascular',
                'title': 'Kardiovaskulárne zdravie',
                'description': 'Pravidelne kontrolujte krvný tlak a konzultujte s kardiológom'
            })
        
        return recommendations
