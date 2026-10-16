import os
import asyncio
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

@app.post("/api/v1/create-event", dependencies=[Depends(verify_api_key)])
async def create_event(payload: PrestationPayload):
    meta = payload.metadonnees
    custom = payload.champs_custom

    # Formatage de la description
    lines = ["DETAILS DE PRESTATION", "==================="]
    for key, val in custom.items():
        if val:
            lines.append(f"{key} : {val}")

    tasks_list = []
    soj = custom.get("Sous-officier de jour")
    if soj:
        tasks_list.append({
            "name": f"Sous-officier de jour : {soj}",
            "type": "TASK"
        })

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
        "autoAccept": True,
        "tasks": tasks_list
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
