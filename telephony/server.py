import os
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from livekit import api
from pydantic import BaseModel

app = FastAPI()

LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "devkey")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "super_secret_key_that_is_at_least_32_characters_long_for_security")


@app.get("/")
def read_root():
    with open("telephony/index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/collaborateur")
def collaborateur_page():
    """Page du collaborateur — pour rejoindre une room en tant qu'humain (Phase 5)."""
    with open("telephony/collaborateur.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/token")
def get_token(participant_name: str = "caller", room_name: str = "accueil"):
    token = api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET) \
        .with_identity(participant_name) \
        .with_name(participant_name) \
        .with_grants(api.VideoGrants(room_join=True, room=room_name))
    return {"token": token.to_jwt()}
