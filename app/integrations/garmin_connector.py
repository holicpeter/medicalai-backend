"""
Garmin Connect API integrácia
Podporuje import dát z Garmin hodinek cez Garmin Connect API
"""
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import json
from pathlib import Path
from garminconnect import (
    Garmin,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
    GarminConnectAuthenticationError,
)

from app.config import settings


class GarminConnector:
    """Konektor pre Garmin Connect API"""
    
    def __init__(self):
        self.client = None
        self.email = None
        self.password = None
        self.is_authenticated = False
        
    async def authenticate(self, email: str, password: str) -> bool:
        """
        Prihlásenie do Garmin Connect
        """
        try:
            self.email = email
            self.password = password
            self.client = Garmin(email, password)
            await asyncio.to_thread(self.client.login)
            self.is_authenticated = True
            print(f"[GARMIN] Successfully authenticated for {email}")
            return True
        except GarminConnectAuthenticationError as e:
            print(f"[GARMIN ERROR] Authentication failed: {e}")
            self.is_authenticated = False
            return False
        except Exception as e:
            print(f"[GARMIN ERROR] Unexpected error during authentication: {e}")
            self.is_authenticated = False
            return False
    
    async def get_heart_rate_data(self, date: Optional[str] = None) -> Dict[str, Any]:
        """
        Získať dáta srdcovej frekvencie za deň
        Args:
            date: YYYY-MM-DD formát, ak None použije dnes
        """
        if not self.is_authenticated:
            raise Exception("Not authenticated. Call authenticate() first.")
        
        try:
            if date is None:
                date = datetime.now().strftime("%Y-%m-%d")
            
            data = await asyncio.to_thread(self.client.get_heart_rates, date)
            return self._process_heart_rate_data(data, date)
        except Exception as e:
            print(f"[GARMIN ERROR] Failed to get heart rate data: {e}")
            return {}
    
    async def get_sleep_data(self, date: Optional[str] = None) -> Dict[str, Any]:
        """
        Získať dáta o spánku
        """
        if not self.is_authenticated:
            raise Exception("Not authenticated. Call authenticate() first.")
        
        try:
            if date is None:
                date = datetime.now().strftime("%Y-%m-%d")
            
            data = await asyncio.to_thread(self.client.get_sleep_data, date)
            return self._process_sleep_data(data, date)
        except Exception as e:
            print(f"[GARMIN ERROR] Failed to get sleep data: {e}")
            return {}
    
    async def get_stress_data(self, date: Optional[str] = None) -> Dict[str, Any]:
        """
        Získať dáta o strese
        """
        if not self.is_authenticated:
            raise Exception("Not authenticated. Call authenticate() first.")
        
        try:
            if date is None:
                date = datetime.now().strftime("%Y-%m-%d")
            
            data = await asyncio.to_thread(self.client.get_stress_data, date)
            return self._process_stress_data(data, date)
        except Exception as e:
            print(f"[GARMIN ERROR] Failed to get stress data: {e}")
            return {}
    
    async def get_steps_data(self, date: Optional[str] = None) -> Dict[str, Any]:
        """
        Získať dáta o krokoch
        """
        if not self.is_authenticated:
            raise Exception("Not authenticated. Call authenticate() first.")
        
        try:
            if date is None:
                date = datetime.now().strftime("%Y-%m-%d")
            
            data = await asyncio.to_thread(self.client.get_steps_data, date)
            return self._process_steps_data(data, date)
        except Exception as e:
            print(f"[GARMIN ERROR] Failed to get steps data: {e}")
            return {}
    
    async def get_body_composition(self, date: Optional[str] = None) -> Dict[str, Any]:
        """
        Získať telesné zloženie (váha, BMI, tuk, voda, svaly)
        """
        if not self.is_authenticated:
            raise Exception("Not authenticated. Call authenticate() first.")
        
        try:
            if date is None:
                date = datetime.now().strftime("%Y-%m-%d")
            
            # Garmin API pre body composition
            start_date = datetime.strptime(date, "%Y-%m-%d")
            end_date = start_date
            
            data = await asyncio.to_thread(
                self.client.get_weigh_ins,
                start_date.strftime("%Y-%m-%d"),
                end_date.strftime("%Y-%m-%d")
            )
            return self._process_body_composition(data, date)
        except Exception as e:
            print(f"[GARMIN ERROR] Failed to get body composition: {e}")
            return {}
    
    async def get_daily_summary(self, date: Optional[str] = None) -> Dict[str, Any]:
        """
        Získať komplexný denný súhrn všetkých metrík
        """
        if not self.is_authenticated:
            raise Exception("Not authenticated. Call authenticate() first.")
        
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")
        
        summary = {
            "date": date,
            "heart_rate": await self.get_heart_rate_data(date),
            "sleep": await self.get_sleep_data(date),
            "stress": await self.get_stress_data(date),
            "steps": await self.get_steps_data(date),
            "body_composition": await self.get_body_composition(date),
        }
        
        return summary
    
    async def get_historical_data(self, days: int = 30) -> List[Dict[str, Any]]:
        """
        Získať historické dáta za posledných X dní
        """
        if not self.is_authenticated:
            raise Exception("Not authenticated. Call authenticate() first.")
        
        historical_data = []
        end_date = datetime.now()
        
        for i in range(days):
            date = (end_date - timedelta(days=i)).strftime("%Y-%m-%d")
            print(f"[GARMIN] Fetching data for {date}...")
            
            try:
                daily_data = await self.get_daily_summary(date)
                historical_data.append(daily_data)
            except Exception as e:
                print(f"[GARMIN ERROR] Failed to fetch data for {date}: {e}")
                continue
        
        return historical_data
    
    def _process_heart_rate_data(self, data: Dict, date: str) -> Dict[str, Any]:
        """Spracuje surové dáta srdcovej frekvencie"""
        if not data:
            return {}
        
        return {
            "date": date,
            "resting_heart_rate": data.get("restingHeartRate"),
            "max_heart_rate": data.get("maxHeartRate"),
            "min_heart_rate": data.get("minHeartRate"),
            "avg_heart_rate": data.get("averageHeartRate"),
            "heart_rate_values": data.get("heartRateValues", []),
        }
    
    def _process_sleep_data(self, data: Dict, date: str) -> Dict[str, Any]:
        """Spracuje surové dáta spánku"""
        if not data or "dailySleepDTO" not in data:
            return {}
        
        sleep_dto = data["dailySleepDTO"]
        
        return {
            "date": date,
            "sleep_start": sleep_dto.get("sleepStartTimestampGMT"),
            "sleep_end": sleep_dto.get("sleepEndTimestampGMT"),
            "total_sleep_seconds": sleep_dto.get("sleepTimeSeconds"),
            "deep_sleep_seconds": sleep_dto.get("deepSleepSeconds"),
            "light_sleep_seconds": sleep_dto.get("lightSleepSeconds"),
            "rem_sleep_seconds": sleep_dto.get("remSleepSeconds"),
            "awake_seconds": sleep_dto.get("awakeSleepSeconds"),
            "sleep_score": sleep_dto.get("sleepScores", {}).get("overall", {}).get("value"),
        }
    
    def _process_stress_data(self, data: Dict, date: str) -> Dict[str, Any]:
        """Spracuje surové dáta stresu"""
        if not data:
            return {}
        
        return {
            "date": date,
            "avg_stress_level": data.get("avgStressLevel"),
            "max_stress_level": data.get("maxStressLevel"),
            "stress_duration_seconds": data.get("stressDuration"),
            "rest_duration_seconds": data.get("restStressDuration"),
            "low_stress_duration": data.get("lowStressDuration"),
            "medium_stress_duration": data.get("mediumStressDuration"),
            "high_stress_duration": data.get("highStressDuration"),
        }
    
    def _process_steps_data(self, data: Dict, date: str) -> Dict[str, Any]:
        """Spracuje surové dáta krokov"""
        if not data:
            return {}
        
        return {
            "date": date,
            "total_steps": data.get("totalSteps"),
            "step_goal": data.get("dailyStepGoal"),
            "total_distance_meters": data.get("totalDistanceMeters"),
            "active_calories": data.get("activeKilocalories"),
        }
    
    def _process_body_composition(self, data: List, date: str) -> Dict[str, Any]:
        """Spracuje surové dáta telesného zloženia"""
        if not data:
            return {}
        
        # Vezmeme najnovšie meranie
        latest = data[0] if isinstance(data, list) else data
        
        return {
            "date": date,
            "weight_kg": latest.get("weight") / 1000 if latest.get("weight") else None,  # Garmin vracia gramy
            "bmi": latest.get("bmi"),
            "body_fat_percentage": latest.get("bodyFat"),
            "body_water_percentage": latest.get("bodyWater"),
            "bone_mass_kg": latest.get("boneMass"),
            "muscle_mass_kg": latest.get("muscleMass"),
        }


# Singleton instance
_garmin_connector = None


def get_garmin_connector() -> GarminConnector:
    """Získať singleton instanciu Garmin konektora"""
    global _garmin_connector
    if _garmin_connector is None:
        _garmin_connector = GarminConnector()
    return _garmin_connector
