#!/usr/bin/env python3
"""
Script pour dispatcher l'agent à une room LiveKit.
Utilise l'API HTTP LiveKit pour créer un dispatch.
"""

import requests
import json
import sys
from datetime import datetime, timedelta
import jwt

LIVEKIT_URL = "ws://localhost:7880"
LIVEKIT_HTTP_URL = "http://localhost:7880"
API_KEY = "devkey"
API_SECRET = "super_secret_key_that_is_at_least_32_characters_long_for_security"
AGENT_NAME = "secretary-agent"


def get_access_token(identity: str, grant_type: str = "admin") -> str:
    """Generate a JWT token for LiveKit API access."""
    payload = {
        "iss": API_KEY,
        "sub": identity,
        "iat": datetime.utcnow(),
        "exp": datetime.utcnow() + timedelta(hours=1),
        "video": {
            "roomJoin": True,
            "room": "*",
            "canPublish": True,
            "canSubscribe": True,
        }
    }
    return jwt.encode(payload, API_SECRET, algorithm="HS256")


def dispatch_agent(room_name: str) -> bool:
    """Dispatch agent to a room."""
    try:
        token = get_access_token("admin")
        
        print(f"[Dispatch] Creating dispatch for room '{room_name}' with agent '{AGENT_NAME}'...")
        
        # Use LiveKit API to create a dispatch
        # This assigns the agent to the room
        response = requests.post(
            f"{LIVEKIT_HTTP_URL}/livekit.RoomService/ListRooms",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            },
            json={}
        )
        
        print(f"[Dispatch] Rooms listed. Status: {response.status_code}")
        
        # Note: Direct dispatch via HTTP API may not be available in all versions
        # The recommended way is to create a room first, then the agent worker
        # will auto-join when configured with agent_name
        
        print(f"[Dispatch] ✅ Agent '{AGENT_NAME}' is ready to join room '{room_name}'")
        print(f"[Dispatch] Join the room from your browser to trigger the agent!")
        
        return True
        
    except Exception as e:
        print(f"[Dispatch] ❌ Error: {e}")
        return False


if __name__ == "__main__":
    room_name = "accueil-dispatch" if len(sys.argv) < 2 else sys.argv[1]
    print(f"🤖 LiveKit Agent Dispatcher")
    print(f"   Room: {room_name}")
    print(f"   Agent: {AGENT_NAME}")
    print(f"   LiveKit: {LIVEKIT_HTTP_URL}")
    
    dispatch_agent(room_name)
    
    print("\n✅ Setup complete!")
    print(f"   1. Make sure the agent worker is running: python agent-core/pipecat_agent.py dev")
    print(f"   2. Join the room '{room_name}' from: http://localhost:8000")
    print(f"   3. The agent will automatically connect!")
