"""
Google Calendar API integrácia
Umožňuje synchronizáciu udalostí z Google Calendar pre analýzu korelácií
"""
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import json
from pathlib import Path
import pickle
import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']


class CalendarConnector:
    """Konektor pre Google Calendar API"""
    
    def __init__(self, credentials_path: Optional[str] = None, token_path: Optional[str] = None):
        self.credentials_path = credentials_path or "credentials.json"
        self.token_path = token_path or "token.pickle"
        self.service = None
        self.is_authenticated = False
    
    def authenticate(self) -> bool:
        """
        Autentifikácia cez Google OAuth2
        """
        creds = None
        
        # Načítať uložené credentials
        if os.path.exists(self.token_path):
            with open(self.token_path, 'rb') as token:
                creds = pickle.load(token)
        
        # Ak credentials neexistujú alebo sú neplatné, autentifikovať
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not os.path.exists(self.credentials_path):
                    print(f"[CALENDAR ERROR] Credentials file not found: {self.credentials_path}")
                    print("Please download credentials.json from Google Cloud Console")
                    return False
                
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_path, SCOPES
                )
                creds = flow.run_local_server(port=0)
            
            # Uložiť credentials pre budúce použitie
            with open(self.token_path, 'wb') as token:
                pickle.dump(creds, token)
        
        try:
            self.service = build('calendar', 'v3', credentials=creds)
            self.is_authenticated = True
            print("[CALENDAR] Successfully authenticated")
            return True
        except Exception as e:
            print(f"[CALENDAR ERROR] Failed to build service: {e}")
            return False
    
    def get_events(
        self, 
        days_back: int = 30,
        days_forward: int = 7,
        calendar_id: str = 'primary'
    ) -> List[Dict[str, Any]]:
        """
        Získať udalosti z kalendára
        """
        if not self.is_authenticated:
            raise Exception("Not authenticated. Call authenticate() first.")
        
        try:
            # Časový rozsah
            time_min = (datetime.utcnow() - timedelta(days=days_back)).isoformat() + 'Z'
            time_max = (datetime.utcnow() + timedelta(days=days_forward)).isoformat() + 'Z'
            
            print(f"[CALENDAR] Fetching events from {time_min} to {time_max}")
            
            events_result = self.service.events().list(
                calendarId=calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                maxResults=500,
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            
            events = events_result.get('items', [])
            processed_events = []
            
            for event in events:
                processed_event = self._process_event(event)
                processed_events.append(processed_event)
            
            print(f"[CALENDAR] Found {len(processed_events)} events")
            return processed_events
            
        except HttpError as error:
            print(f"[CALENDAR ERROR] An error occurred: {error}")
            return []
    
    def get_events_for_date(self, date: str, calendar_id: str = 'primary') -> List[Dict[str, Any]]:
        """
        Získať udalosti pre konkrétny deň (YYYY-MM-DD)
        """
        if not self.is_authenticated:
            raise Exception("Not authenticated. Call authenticate() first.")
        
        try:
            # Časový rozsah pre daný deň
            date_obj = datetime.strptime(date, "%Y-%m-%d")
            time_min = date_obj.replace(hour=0, minute=0, second=0).isoformat() + 'Z'
            time_max = date_obj.replace(hour=23, minute=59, second=59).isoformat() + 'Z'
            
            events_result = self.service.events().list(
                calendarId=calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                maxResults=100,
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            
            events = events_result.get('items', [])
            processed_events = [self._process_event(event) for event in events]
            
            return processed_events
            
        except HttpError as error:
            print(f"[CALENDAR ERROR] An error occurred: {error}")
            return []
    
    def analyze_event_categories(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Analyzuje kategórie udalostí a ich frekvenciu
        """
        categories = {
            "work": 0,
            "meeting": 0,
            "sport": 0,
            "health": 0,
            "travel": 0,
            "social": 0,
            "other": 0
        }
        
        work_keywords = ["meeting", "work", "call", "presentation", "deadline", "porada", "práca"]
        sport_keywords = ["gym", "sport", "workout", "run", "bike", "fitness", "cvičenie", "beh"]
        health_keywords = ["doctor", "hospital", "checkup", "lekár", "nemocnica", "prehliadka"]
        travel_keywords = ["flight", "train", "travel", "trip", "let", "vlak", "cesta"]
        social_keywords = ["party", "dinner", "lunch", "coffee", "vecera", "obed", "káva"]
        
        for event in events:
            title = event.get("summary", "").lower()
            
            if any(kw in title for kw in work_keywords):
                categories["work"] += 1
            elif any(kw in title for kw in sport_keywords):
                categories["sport"] += 1
            elif any(kw in title for kw in health_keywords):
                categories["health"] += 1
            elif any(kw in title for kw in travel_keywords):
                categories["travel"] += 1
            elif any(kw in title for kw in social_keywords):
                categories["social"] += 1
            else:
                categories["other"] += 1
        
        total = sum(categories.values())
        
        return {
            "categories": categories,
            "total_events": total,
            "busiest_category": max(categories, key=categories.get) if total > 0 else None
        }
    
    def _process_event(self, event: Dict) -> Dict[str, Any]:
        """Spracuje surovú udalosť z API"""
        start = event['start'].get('dateTime', event['start'].get('date'))
        end = event['end'].get('dateTime', event['end'].get('date'))
        
        # Parsovať datetime
        if 'T' in start:
            start_dt = datetime.fromisoformat(start.replace('Z', '+00:00'))
            end_dt = datetime.fromisoformat(end.replace('Z', '+00:00'))
            duration_minutes = int((end_dt - start_dt).total_seconds() / 60)
        else:
            start_dt = datetime.strptime(start, "%Y-%m-%d")
            end_dt = datetime.strptime(end, "%Y-%m-%d")
            duration_minutes = None  # Celodenná udalosť
        
        return {
            "id": event.get('id'),
            "summary": event.get('summary', 'No Title'),
            "description": event.get('description', ''),
            "location": event.get('location', ''),
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "duration_minutes": duration_minutes,
            "attendees": len(event.get('attendees', [])),
            "is_all_day": 'T' not in start,
        }


# Singleton instance
_calendar_connector = None


def get_calendar_connector() -> CalendarConnector:
    """Získať singleton instanciu Calendar konektora"""
    global _calendar_connector
    if _calendar_connector is None:
        _calendar_connector = CalendarConnector()
    return _calendar_connector
