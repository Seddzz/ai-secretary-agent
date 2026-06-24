# Agent IA Secrétaire Téléphonique

Prototype d'agent conversationnel IA pour l'accueil et la gestion automatisée d'appels téléphoniques, développé dans le cadre d'un stage. Stack 100% gratuite et auto-hébergée — aucune API payante n'est utilisée.

## Architecture

```
Appelant (navigateur, WebRTC)
        │
        ▼
  LiveKit Server (self-hosted, Docker)
        │
        ▼
  Agent Python (telephony/bot.py)
        │
        ├─► VAD (détection de parole par énergie RMS)
        ├─► Whisper (STT, local, CPU)
        ├─► Ollama / llama3.2 (LLM + function-calling)
        └─► Piper TTS (français, local, CPU)
```

| Composant      | Technologie               | Coût  |
|----------------|----------------------------|-------|
| Téléphonie     | LiveKit self-hosted (WebRTC) | Gratuit |
| STT            | Whisper (openai-whisper, modèle `small`) | Gratuit |
| LLM            | Ollama (`llama3.2`)        | Gratuit |
| TTS            | Piper (siwis-medium, français) | Gratuit |
| Frontend       | HTML/JS statique + FastAPI | Gratuit |

## Prérequis

- Docker Desktop
- Python 3.11+ avec un environnement virtuel (`.venv`)
- [Ollama](https://ollama.com) installé, avec le modèle `llama3.2` téléchargé :
  ```
  ollama pull llama3.2
  ```
- ffmpeg installé et accessible (utilisé par Whisper pour décoder l'audio)
- Les dépendances Python installées via `uv` :
  ```
  uv add livekit livekit-agents livekit-plugins-silero fastapi uvicorn python-dotenv aiohttp numpy openai-whisper piper-tts
  ```

**Voix Piper (TTS)**

Le modèle vocal français doit être téléchargé dans le dossier `voices/` :
```bash
mkdir -p voices
curl -L -o voices/fr_FR-siwis-medium.onnx "https://huggingface.co/rhasspy/piper-voices/resolve/main/fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx"
curl -L -o voices/fr_FR-siwis-medium.onnx.json "https://huggingface.co/rhasspy/piper-voices/resolve/main/fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx.json"
```

## Configuration

Créer un fichier `.env` à la racine du projet :

```
LIVEKIT_URL=ws://localhost:7880
LIVEKIT_API_KEY=<clé générée, 32+ caractères>
LIVEKIT_API_SECRET=<secret généré, 32+ caractères>
OLLAMA_URL=http://localhost:11434
```

> ⚠️ Ne jamais utiliser les clés par défaut `devkey` / `secret` en dehors d'un test rapide — LiveKit refuse de démarrer proprement avec un secret trop court (< 32 caractères).

## Lancer le projet

Trois commandes, dans trois terminaux séparés, **dans cet ordre** :

**1. Serveur LiveKit (Docker)**
```
docker compose up
```
Attendre la ligne `starting LiveKit server` dans les logs avant de continuer.

**2. Vérifier qu'Ollama tourne**
```
ollama list
```
Si la commande échoue, lancer le service manuellement :
```
ollama serve
```

**3. L'agent IA (STT + LLM + TTS)**
```
python telephony\bot.py
```
Attendre les lignes `Agent connecté à la room 'accueil'` puis `[TTS] Audio track published (Piper -> LiveKit).`

**4. Le serveur web (frontend + génération de tokens)**
```
uvicorn telephony.server:app --reload --port 8000
```

Une fois les 4 process actifs, ouvrir **http://localhost:8000** dans le navigateur, autoriser le micro, et parler. Après une pause de silence (~2 secondes), le pipeline transcrit la phrase (Whisper), génère une réponse (Ollama), la synthétise en audio (Piper) et la diffuse vers le navigateur. L'audio de l'agent est audible via les haut-parleurs. Si l'appelant parle pendant la réponse de l'agent (barge-in), celle-ci est interrompue automatiquement.

## Intégrations métier — Phase 6 (n8n self-hosted)

À la fin de chaque appel, l'agent envoie un webhook au workflow n8n qui déclenche 3 actions.

### 1. Lancer n8n

```bash
docker compose up     # n8n démarre sur http://localhost:5678
```
Premier accès : créer un compte admin local (gratuit, stocké dans `n8n_data`).

### 2. Importer le workflow

Dans n8n → **Workflows** → **Import from File** → `integrations/n8n_workflow.json`.

Le workflow :
```
Webhook (/webhook/appel-secrétaire)
        │
        ▼
  Préparer données (code)
        │
   ┌────┼────────────┬──────────────┐
   ▼    ▼            ▼              ▼
Google   Google      Email récap
Sheets   Calendar    (Gmail)
(CRM)    (si urgence)
```

### 3. Connecter les credentials Google (obligatoire)

n8n ne stocke jamais les credentials dans le JSON exporté — il faut les créer dans l'UI :

1. **Settings → Credentials → Add** → choisir *Google Sheets OAuth2 API*, *Google Calendar OAuth2 API*, *Gmail OAuth2 API*.
2. Suivre l'auth Google OAuth (compte Google gratuit, quota largement suffisant pour un prototype).
3. Dans chaque nœud du workflow (CRM, Agenda, Email), sélectionner le credential correspondant dans le menu déroulant.
4. Dans le nœud **CRM (Google Sheets)**, remplacer `REMPLACER_PAR_ID_DU_GOOGLE_SHEET` par l'ID de votre Google Sheet (son onglet doit s'appeler `Appels` avec colonnes : nom, numéro, motif, urgence, service, statut, résumé, date).

### 4. Variable d'environnement

Le webhook est déclenché automatiquement si `N8N_WEBHOOK_URL` est défini dans `.env` :
```
N8N_WEBHOOK_URL=http://localhost:5678/webhook/appel-secrétaire
EMAIL_DESTINATAIRE=responsable@acme-conseil.ma
```
Si la variable est absente, l'intégration est désactivée (silencieusement) — l'appel reste sauvé en SQLite.

> Le webhook est **non bloquant** : si n8n est arrêté ou injoignable, l'agent continue de fonctionner normalement et journalise juste un avertissement.

## Structure du projet

```
ai-secretary-agent/
├── telephony/
│   ├── bot.py              # Agent principal : LiveKit + pipeline audio/IA + VAD + barge-in
│   ├── server.py           # Serveur FastAPI : frontend appelant + collaborateur + tokens
│   ├── index.html          # Interface web de l'appelant (appel, raccrocher, transfert)
│   └── collaborateur.html  # Interface web du collaborateur (rejoindre un service)
├── agent-core/
│   ├── conversation.py     # Logique : FAQ, machine d'états, function-calling, prompt
│   ├── faq.json            # Base de connaissances FAQ
│   └── scenarios.md        # Définition des scénarios conversationnels
├── db/
│   └── models.py           # Persistance SQLite + export CSV
├── integrations/
│   └── n8n_workflow.json   # Workflow n8n (webhook + Sheets + Calendar + Gmail)
├── docker-compose.yml      # LiveKit + n8n + Postgres
├── voices/                 # Modèles Piper TTS (téléchargés, non versionnés)
├── .env                    # Variables d'environnement (non versionné)
└── README.md
```

## État d'avancement

- ✅ Connexion WebRTC appelant ↔ serveur LiveKit
- ✅ Connexion de l'agent à la room et abonnement au flux audio
- ✅ Transcription vocale (Whisper, local)
- ✅ Génération de réponse structurée (Ollama / llama3.2, local)
- ✅ Synthèse vocale de la réponse (Piper TTS, local, français)
- ✅ Publication audio vers l'appelant (LiveKit audio track)
- ✅ VAD par énergie (RMS) — détection de parole en temps réel
- ✅ Gestion des interruptions (barge-in) — annule TTS + LLM en cours
- ✅ Scénarios conversationnels structurés (accueil, FAQ, message, qualification, transfert)
- ✅ Function-calling (enregistrer_message, qualifier_demande, transferer_appel, consulter_faq)
- ✅ Base de connaissances FAQ (JSON + recherche par similarité lexicale)
- ✅ Transfert simulé vers un humain (second participant LiveKit + page collaborateur)
- ✅ Persistance de l'historique des appels (SQLite + export CSV)
- ✅ Intégrations métier (agenda, CRM, email via n8n self-hosted) — Phase 6
- ⬜ Tests et mesure de performance — Phase 8
- ⬜ Documentation finale + slides — Phase 9

## Endpoints & outils

| URL / commande | Rôle |
|----------------|------|
| `http://localhost:8000` | Interface appelant (simule un appel entrant) |
| `http://localhost:8000/collaborateur` | Interface collaborateur (reçoit les transferts) |
| `python db/models.py` | Liste les appels enregistrés |
| `python db/models.py export` | Exporte les appels en CSV |

## Notes techniques importantes

- **Nom de room** : l'agent (`bot.py`) et le frontend (`server.py` / `index.html`) doivent utiliser **exactement le même nom de room** (`accueil` par défaut). Un nom différent empêche l'agent et l'appelant de se rencontrer, sans qu'aucune erreur explicite n'apparaisse.
- **ffmpeg sur Windows** : si Whisper renvoie `[WinError 2] The system cannot find the file specified`, ffmpeg n'est pas visible dans le PATH du process Python. Le contournement appliqué dans `bot.py` force l'ajout du dossier `bin` de ffmpeg au PATH au runtime, sans dépendre du PATH système.
- **Performance Whisper** : le modèle est chargé une seule fois au démarrage (variable globale `WHISPER_MODEL`) et exécuté dans un thread séparé (`asyncio.to_thread`) pour ne pas bloquer la réception audio pendant la transcription.
- **VAD** : la détection de parole repose sur l'énergie RMS (hystérésis 400/250, silence 700 ms). Aucune dépendance externe. Les seuils sont tunables en haut de `bot.py` (`VAD_START_RMS`, `VAD_STOP_RMS`, `VAD_SILENCE_MS`).
- **Barge-in** : si l'appelant parle pendant la réponse de l'agent, le TTS en cours ET la requête LLM en cours sont annulés, évitant l'empilement de réponses.
- **Coût total de l'infrastructure : 0 DH** — stack entièrement open source et auto-hébergée, conforme aux contraintes du projet (étudiant, aucune dépense d'API).