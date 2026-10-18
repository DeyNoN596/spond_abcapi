import os
import asyncio
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build
from fastapi import FastAPI, HTTPException, Security, Depends
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from spond import spond
from datetime import datetime
from zoneinfo import ZoneInfo

app = FastAPI(title="Spond API Gateway")

# Gestion de la clé API reçue d'Excel
API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=True)

# Variables d'environnement récupérées depuis la plateforme d'hébergement
EXPECTED_API_KEY = os.getenv("API_SECRET_KEY")
SPOND_USERNAME = os.getenv("SPOND_USER")
SPOND_PASSWORD = os.getenv("SPOND_PASS")
SPOND_GROUP_ID = os.getenv("SPOND_GROUP", "5147B24128CF42D4A2B6F8832074B0D5")

SCOPES = ['https://www.googleapis.com/auth/calendar.events']

def get_calendar_service():
    # On récupère le contenu du fichier JSON depuis une variable d'environnement sur Render
    google_credentials_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
    
    if not google_credentials_json:
        raise HTTPException(status_code=500, detail="Variables de compte de service Google manquantes")
        
    creds_dict = json.loads(google_credentials_json)
    creds = service_account.Credentials.from_service_account_info(
        creds_dict, scopes=SCOPES
    )
    
    # Construction du client API Google Calendar
    return build('calendar', 'v3', credentials=creds)
    

class PrestationPayload(BaseModel):
    metadonnees: dict
    champs_custom: dict

def format_iso_france(date_str: str, heure_str: str) -> str:
    dt_naive = datetime.strptime(f"{date_str} {heure_str}", "%Y-%m-%d %H:%M")
    dt_france = dt_naive.replace(tzinfo=ZoneInfo("Europe/Paris"))
    return dt_france.isoformat()

async def verify_api_key(api_key: str = Depends(API_KEY_HEADER)):
    if api_key != EXPECTED_API_KEY:
        raise HTTPException(status_code=403, detail="Clé API invalide")
    return api_key

# Route pour Google Calendar
@app.post("/api/v1/create-event-google", dependencies=[Depends(verify_api_key)])
async def create_google_event(payload: PrestationPayload):
    meta = payload.metadonnees
    custom = payload.champs_custom

    # Formatage de la description
    lines = ["FICHE DE PRESTATION", "==================="]
    for key, val in custom.items():
        if val and key != "Details":
            lines.append(f"{key} : {val}")

    if custom.get("Details"):
        lines.append("\nINFORMATIONS COMPLÉMENTAIRES")
        lines.append("----------------------------")
        lines.append(custom["Details"])

    # Conversion des dates ISO (zone Europe/Paris)
    start_iso = format_iso_france(meta['date_depart'], meta['heure_depart'])
    end_iso = format_iso_france(meta['date_retour'], meta['heure_retour'])
    titre = f"🎺 {meta.get('titre', 'Prestation')} – {meta.get('ville', '')}"

    # Structure attendue par l'API Google Calendar v3
    google_event_body = {
        "summary": titre,
        "location": meta.get("lieu_rdv", ""),
        "description": "\n".join(lines),
        "start": {
            "dateTime": start_iso,
            "timeZone": "Europe/Paris"
        },
        "end": {
            "dateTime": end_iso,
            "timeZone": "Europe/Paris"
        }
    }

    # Envoi à Google Calendar
    try:
        service = get_calendar_service()
        # L'ID de l'agenda partagé (souvent l'adresse e-mail de l'agenda principal ou l'ID de l'agenda cible)
        calendar_id = os.getenv("GOOGLE_CALENDAR_ID", "primary")
        
        event = service.events().insert(calendarId=calendar_id, body=google_event_body).execute()
        return {"status": "success", "google_event_id": event.get("id")}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur Google Calendar API: {str(e)}")

    return {"status": "success", "message": "Événement Google Agenda préparé"}

#route Spond
@app.post("/api/v1/create-event-spond", dependencies=[Depends(verify_api_key)])
async def create_spond_event(payload: PrestationPayload):
    meta = payload.metadonnees
    custom = payload.champs_custom

    # Formatage de la description
    lines = ["DETAILS DE PRESTATION", "==================="]
    for key, val in custom.items():
        if val:
            lines.append(f"{key} : {val}")

    start_iso = format_iso_france(meta['date_depart'], meta['heure_depart'])
    end_iso = format_iso_france(meta['date_retour'], meta['heure_retour'])
    
    titre = f"🎺 {meta.get('titre', 'Prestation')} – {meta.get('ville', '')}"

    spond_body = {
        "heading": titre,
        "description": "\n".join(lines),
        "location": {"feature": meta.get("lieu_rdv", "")},
        "startTimestamp": start_iso,
        "endTimestamp": end_iso,
        "group": {"id": SPOND_GROUP_ID},
        "recipients": {"group": {"id": SPOND_GROUP_ID}},
        "type": "EVENT",
        "autoAccept": True
    }


    spond_client = spond.Spond(username=SPOND_USERNAME, password=SPOND_PASSWORD)
    try:
        await spond_client.login()
        session = getattr(spond_client, "clientsession", getattr(spond_client, "_session", None))
        base_url = getattr(spond_client, "api_url", "https://api.spond.com/spond/v1/").rstrip('/')

        headers = {
            "Authorization": f"Bearer {spond_client.token}",
            "Content-Type": "application/json"
        }


        async with session.post(f"{base_url}/sponds", json=spond_body, headers=headers) as resp:
            if resp.status in (200, 201):
                res = await resp.json()
                return {"status": "success", "event_id": res.get("id")}
            else:
                err_msg = await resp.text()
                raise HTTPException(status_code=resp.status, detail=f"Spond Error: {err_msg}")
    finally:
        session = getattr(spond_client, "clientsession", getattr(spond_client, "_session", None))
        if session and not session.closed:
            await session.close()
        await asyncio.sleep(0.250)
