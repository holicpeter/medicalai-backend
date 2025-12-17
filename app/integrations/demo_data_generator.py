"""
Demo/Test data generator pre Garmin integráciu
Použite, ak ešte nemáte Garmin hodinky
"""
from datetime import datetime, timedelta
import json
import random
from pathlib import Path


def generate_demo_garmin_data(days: int = 30) -> list:
    """Vygeneruje demo Garmin dáta pre testovanie"""
    
    demo_data = []
    end_date = datetime.now()
    
    for i in range(days):
        date = (end_date - timedelta(days=i)).strftime("%Y-%m-%d")
        
        # Simulácia realistických hodnôt
        day_of_week = (end_date - timedelta(days=i)).weekday()
        is_weekend = day_of_week >= 5
        
        # Víkend = menej stresu, viac spánku, menej krokov
        stress_base = 45 if is_weekend else 58
        sleep_base = 8.2 if is_weekend else 7.0
        steps_base = 6000 if is_weekend else 9500
        
        # Pridať náhodné odchýlky
        daily_data = {
            "date": date,
            "heart_rate": {
                "date": date,
                "resting_heart_rate": random.randint(55, 75),
                "max_heart_rate": random.randint(140, 180),
                "min_heart_rate": random.randint(48, 58),
                "avg_heart_rate": random.randint(68, 85),
            },
            "sleep": {
                "date": date,
                "total_sleep_seconds": int((sleep_base + random.uniform(-0.8, 0.8)) * 3600),
                "deep_sleep_seconds": int(random.uniform(1.2, 2.0) * 3600),
                "light_sleep_seconds": int(random.uniform(3.5, 4.5) * 3600),
                "rem_sleep_seconds": int(random.uniform(1.5, 2.2) * 3600),
                "awake_seconds": int(random.uniform(0.3, 0.8) * 3600),
                "sleep_score": random.randint(65, 92),
            },
            "stress": {
                "date": date,
                "avg_stress_level": int(stress_base + random.uniform(-12, 15)),
                "max_stress_level": int(stress_base + random.uniform(20, 40)),
                "stress_duration_seconds": int(random.uniform(4, 8) * 3600),
                "rest_duration_seconds": int(random.uniform(2, 4) * 3600),
            },
            "steps": {
                "date": date,
                "total_steps": int(steps_base + random.uniform(-2000, 3000)),
                "step_goal": 10000,
                "total_distance_meters": int((steps_base + random.uniform(-2000, 3000)) * 0.75),
                "active_calories": random.randint(1800, 2800),
            },
            "body_composition": {
                "date": date,
                "weight_kg": round(75 + random.uniform(-1, 1), 1),
                "bmi": round(24 + random.uniform(-0.5, 0.5), 1),
                "body_fat_percentage": round(18 + random.uniform(-2, 2), 1),
                "body_water_percentage": round(60 + random.uniform(-3, 3), 1),
            }
        }
        
        demo_data.append(daily_data)
    
    return demo_data


def save_demo_data(days: int = 30):
    """Uloží demo dáta do súboru"""
    data = generate_demo_garmin_data(days)
    
    # Vytvoriť adresár
    data_dir = Path("data/garmin")
    data_dir.mkdir(parents=True, exist_ok=True)
    
    # Uložiť
    filename = f"garmin_demo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    filepath = data_dir / filename
    
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    
    print(f"✓ Demo dáta uložené do: {filepath}")
    print(f"✓ Vygenerovaných {len(data)} dní")
    
    return data


if __name__ == "__main__":
    print("Generujem demo Garmin dáta...")
    save_demo_data(30)
