from dotenv import load_dotenv
load_dotenv()

import os
os.environ["PATH"] = r"C:\Users\zines\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.1-full_build\bin" + os.pathsep + os.environ["PATH"]

import asyncio
import logging
import json
import numpy as np
import aiohttp
from pathlib import Path
from datetime import datetime

from livekit import rtc, api

import whisper  # STT (local, CPU)
from piper.voice import PiperVoice  # TTS (local, CPU)
import importlib

# Phase 4 — Logique conversationnelle (FAQ + machine d'états + function-calling)
_conv = importlib.import_module("agent-core.conversation")

# Phase 7 — Persistance SQLite
_db = importlib.import_module("db.models")

# ✅ LOAD MODELS ONCE AT STARTUP
WHISPER_MODEL = whisper.load_model("small")

# ✅ Piper French voice (siwis, medium). Téléchargé dans voices/ au premier setup.
_PIPER_MODEL = Path(__file__).resolve().parent.parent / "voices" / "fr_FR-siwis-medium.onnx"
PIPER_VOICE = PiperVoice.load(str(_PIPER_MODEL))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "ws://localhost:7880")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "devkey")
LIVEKIT_API_SECRET = os.getenv(
    "LIVEKIT_API_SECRET",
    "super_secret_key_that_is_at_least_32_characters_long_for_security"
)
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = "llama3.2"
ROOM_NAME = "accueil"

# Audio constants
STT_SAMPLE_RATE = 16000          # Whisper / entrée appelant
TTS_SAMPLE_RATE = 22050          # Piper siwIS sortie
TTS_FRAME_MS = 20                # granularité de publication audio (ms)
TTS_FRAME_SAMPLES = int(TTS_SAMPLE_RATE * TTS_FRAME_MS / 1000)  # 441 échantillons

# --- VAD (Voice Activity Detection) basé sur l'énergie (RMS) ---
# Détection robuste de parole, alternative légère à Silero. Évite de transcrire
# du silence/bruit ambiant : on accumule l'audio seulement pendant la parole,
# et on déclenche la transcription après une vraie pause de l'appelant.
VAD_FRAME_MS = 30
VAD_FRAME_SAMPLES = int(STT_SAMPLE_RATE * VAD_FRAME_MS / 1000)  # 480 échantillons @16kHz
VAD_START_RMS = 400     # RMS minimum (int16) pour considérer qu'on commence à parler
VAD_STOP_RMS = 250      # RMS maximum (int16) en-dessous duquel on considère le silence
VAD_SILENCE_MS = 700    # durée de silence pour clôturer un énoncé
VAD_MIN_SPEECH_MS = 250 # ignore les éclats < 250 ms (claquements, bruits)
VAD_MAX_UTTERANCE_S = 12  # tronque au-delà (évite OOM / hallucination Whisper)

logger.info(f"Configuration loaded: LIVEKIT_URL={LIVEKIT_URL}, ROOM={ROOM_NAME}")


class AudioBuffer:
    def __init__(self, sample_rate: int = 16000):
        self.buffer = np.array([], dtype=np.int16)
        self.sample_rate = sample_rate

    def add_frame(self, frame: rtc.AudioFrame):
        audio_data = np.frombuffer(frame.data, dtype=np.int16)
        self.buffer = np.concatenate([self.buffer, audio_data])

    def get_wav(self) -> bytes:
        import wave
        import io

        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.sample_rate)
            wav.writeframes(self.buffer.tobytes())

        return wav_buffer.getvalue()

    def clear(self):
        self.buffer = np.array([], dtype=np.int16)


def _rms(samples: np.ndarray) -> float:
    """RMS energy of an int16 audio block. ~300-500 = silence doux, >800 = parole nette."""
    if samples.size == 0:
        return 0.0
    # int32 pour éviter l'overflow au carré
    return float(np.sqrt(np.mean(samples.astype(np.int32) ** 2)))


async def transcribe_audio(audio_buffer: AudioBuffer) -> str:
    """Transcribe audio using Whisper (optimized)."""
    try:
        import tempfile

        wav_data = audio_buffer.get_wav()
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
            f.write(wav_data)
            temp_file = f.name

        logger.info("[Whisper] Transcribing audio (async thread)...")

        # ✅ non-blocking call
        result = await asyncio.to_thread(
            WHISPER_MODEL.transcribe,
            temp_file,
            language="fr"
        )

        text = result.get("text", "").strip()
        logger.info(f"[Whisper] Transcribed: {text}")

        os.unlink(temp_file)
        return text

    except Exception as e:
        logger.error(f"[Whisper] Error: {e}")
        return ""


async def _ollama_generate(system_prompt: str, user_message: str,
                           chat_history: list) -> str:
    """
    Appel bas niveau à Ollama (/api/generate, format chat via prompt concaténé).
    Renvoie le texte brut produit par le modèle.
    """
    prompt = system_prompt + "\n\n## Historique de conversation\n"
    for msg in chat_history[-6:]:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        prompt += f"{role.upper()}: {content}\n"
    prompt += f"\nUTILISATEUR: {user_message}\nASSISTANT:"

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "temperature": 0.4,   # plus faible = JSON plus fiable
            },
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            if response.status != 200:
                logger.error(f"[Ollama] HTTP {response.status}")
                return ""
            data = await response.json()
            return data.get("response", "").strip()


async def get_agent_turn(user_message: str, chat_history: list,
                         ctx) -> tuple[str, dict]:
    """
    Phase 4 — Tour complet de l'agent :
    1) génère une réponse structurée (JSON) via le LLM,
    2) applique l'extraction des données collectées au contexte,
    3) exécute la fonction demandée (FAQ, message, transfert...).

    Renvoie (texte_à_dire, résultat_fonction).
    Le texte à dire tient compte du résultat de la fonction : si le LLM a demandé
    une FAQ mais que la recherche échoue, on remplace par un message de repli.
    """
    try:
        logger.info("[Ollama] Querying llama3.2 (structured)...")
        raw = await _ollama_generate(_conv.SYSTEM_PROMPT, user_message, chat_history)
        if not raw:
            return "Je n'ai pas pu générer une réponse. Pouvez-vous répéter ?", {}

        parsed = _conv.parse_llm_response(raw)
        reply_text = parsed["reponse"]
        fonction = parsed["fonction"]
        donnees = parsed["donnees_collectees"]
        etat = parsed["etat"]

        # 1) Mise à jour du contexte d'appel
        _conv.apply_extraction(ctx, donnees)
        if etat in {"ACCUEIL", "FAQ", "MESSAGE", "QUALIFICATION", "TRANSFERT", "FIN"}:
            ctx.etat = etat
        logger.info(f"[Conv] etat={etat} | ctx={ctx.__dict__}")

        # 2) Exécution de la fonction
        fn_result = _conv.execute_function(ctx, fonction)
        logger.info(f"[Conv] fonction={fonction.get('nom')} -> {fn_result}")

        # 3) Ajustement de la réponse selon le résultat de la fonction
        fn_name = fonction.get("nom") if isinstance(fonction, dict) else None
        if fn_name == "consulter_faq":
            if not fn_result.get("reponse"):
                # La FAQ n'a rien trouvé -> message de repli au lieu d'inventer
                reply_text = ("Je n'ai pas cette information précise. "
                              "Voulez-vous que je vous mette en relation avec un collaborateur ?")
            else:
                # On fait confiance à la réponse FAQ (source fiable) plutôt qu'au LLM
                reply_text = fn_result["reponse"]
                if ctx.nom_appelant and ctx.faq_consultee is not None:
                    ctx.faq_consultee.append(fn_result.get("id", "?"))

        return reply_text, fn_result

    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(f"[Conv] Error: {e}")
        return "Erreur de communication.", {}


async def synthesize_and_play(text: str, audio_source: rtc.AudioSource,
                              stop_event: asyncio.Event) -> None:
    """
    Étape 16 — Synthétise le texte via Piper TTS (local) et publie l'audio
    dans la room LiveKit, frame par frame au rythme réel (TTS_FRAME_MS).

    Étape 18 — Barge-in : si `stop_event` est setté pendant la lecture
    (l'appelant a parlé), on interrompt immédiatement.
    """
    if not text:
        return

    logger.info(f"[TTS] Synthesizing: {text[:80]}...")

    # 1) Synthèse hors event loop (CPU-bound, non-bloquant)
    try:
        chunks = await asyncio.to_thread(lambda: list(PIPER_VOICE.synthesize(text)))
    except Exception as e:
        logger.error(f"[TTS] Synthesis error: {e}")
        return

    # 2) Concatène tous les chunks int16 en un seul tableau
    all_bytes = b"".join(c.audio_int16_bytes for c in chunks)
    if not all_bytes:
        return
    audio_array = np.frombuffer(all_bytes, dtype=np.int16)

    # 3) Découpe en frames de TTS_FRAME_SAMPLES et publie au rythme réel
    total = len(audio_array)
    n_frames = total // TTS_FRAME_SAMPLES

    played = 0
    for i in range(n_frames):
        if stop_event.is_set():
            logger.info("[TTS] Barge-in detected — stopping playback.")
            audio_source.clear_queue()
            return

        chunk = audio_array[i * TTS_FRAME_SAMPLES:(i + 1) * TTS_FRAME_SAMPLES]
        frame = rtc.AudioFrame(
            data=chunk.tobytes(),
            sample_rate=TTS_SAMPLE_RATE,
            num_channels=1,
            samples_per_channel=TTS_FRAME_SAMPLES,
        )
        await audio_source.capture_frame(frame)
        played += 1

        # Rythme réel : on attend ~TTS_FRAME_MS avant la frame suivante,
        # mais on reste réactif au barge-in (poll fréquent).
        await asyncio.sleep(TTS_FRAME_MS / 1000)

    # Reste éventuel (dernier fragment incomplet)
    if not stop_event.is_set() and total % TTS_FRAME_SAMPLES > 0:
        chunk = audio_array[n_frames * TTS_FRAME_SAMPLES:]
        frame = rtc.AudioFrame(
            data=chunk.tobytes(),
            sample_rate=TTS_SAMPLE_RATE,
            num_channels=1,
            samples_per_channel=len(chunk),
        )
        await audio_source.capture_frame(frame)

    logger.info(f"[TTS] Playback done ({played} frames, ~{played * TTS_FRAME_MS} ms).")


async def main():
    token = api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET) \
        .with_identity("agent_bot") \
        .with_name("AI Secretary") \
        .with_grants(api.VideoGrants(room_join=True, room=ROOM_NAME))

    room = rtc.Room()
    loop = asyncio.get_running_loop()

    # Historique de conversation (textuel, pour le LLM)
    chat_history: list[dict] = []

    # Contexte vivant de l'appel (Phase 4) : collecte progressive d'informations
    call_ctx = _conv.CallContext()
    call_start = datetime.now()

    active_audio_tasks: set[asyncio.Task] = set()

    # --- Phase 7 : Sauvegarde en base ---
    def _save_call(statut: str, transfere: bool = False) -> None:
        """Sauvegarde l'appel en SQLite à la fin de la conversation."""
        duree = int((datetime.now() - call_start).total_seconds())
        transcription = "\n".join(
            f"{'👤' if m.get('role') == 'user' else '🤖'} {m.get('content', '')}"
            for m in chat_history
        )
        try:
            call_id = _db.save_appel(
                participant=call_ctx.nom_appelant or "caller",
                duree_approx_sec=duree,
                transcription=transcription,
                resume=call_ctx.resume_demande or call_ctx.motif or "",
                motif=call_ctx.motif or "",
                urgence=call_ctx.urgence,
                statut=statut,
                transfere=transfere,
                service=call_ctx.service_cible,
                metadata={
                    "messages_enregistres": call_ctx.messages,
                    "etat_final": call_ctx.etat,
                },
            )
            logger.info(f"[DB] Appel sauvegardé (id={call_id}, dur={duree}s, statut={statut})")
        except Exception as e:
            logger.error(f"[DB] Erreur sauvegarde: {e}")

    # --- Phase 6 : Webhook vers n8n (CRM + Agenda + Email) ---
    async def _trigger_n8n_webhook(statut: str, transfere: bool) -> None:
        """
        Phase 6 — Envoie le récapitulatif de l'appel au workflow n8n (self-hosted).
        n8n déclenche ensuite : Google Sheets (CRM), Google Calendar (si urgence),
        Gmail (email récap). Échec non bloquant : l'appel reste sauvé en SQLite.
        """
        webhook_url = os.getenv("N8N_WEBHOOK_URL")
        if not webhook_url:
            logger.info("[n8n] N8N_WEBHOOK_URL non configuré — webhook ignoré.")
            return

        payload = {
            "nom_appelant": call_ctx.nom_appelant,
            "numero": call_ctx.numero,
            "motif": call_ctx.motif,
            "resume_demande": call_ctx.resume_demande or call_ctx.motif,
            "urgence": call_ctx.urgence or "normale",
            "service": call_ctx.service_cible,
            "statut": statut,
            "transfere": transfere,
            "duree_approx_sec": int((datetime.now() - call_start).total_seconds()),
            "date_appel": datetime.now().isoformat(),
            "messages": call_ctx.messages,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    webhook_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status < 300:
                        logger.info(f"[n8n] Webhook envoyé (HTTP {resp.status}).")
                    else:
                        logger.warning(f"[n8n] Webhook HTTP {resp.status}.")
        except Exception as e:
            logger.warning(f"[n8n] Webhook échoué (non bloquant): {e}")

    async def _finalize_call(statut: str, transfere: bool = False) -> None:
        """Phase 6+7 — Sauvegarde SQLite + déclenche le webhook n8n."""
        _save_call(statut, transfere)
        await _trigger_n8n_webhook(statut, transfere)

    # --- Phase 5 : Transfert simulé vers un collaborateur LiveKit ---
    async def _handle_transfer(service: str) -> None:
        """
        Phase 5 — Transfert vers un collaborateur humain :
        1) Envoie le contexte de l'appel (nom, motif, résumé, urgence)
           via data channel à la room pour que le collaborateur le reçoive.
        2) L'agent quitte la room — l'appelant reste connecté et peut
           être rejoint par le collaborateur sur la room `accueil-{service}`.
        """
        nonlocal is_speaking, current_processing_task

        # Arrête tout traitement en cours (plus de réponses IA)
        if current_processing_task and not current_processing_task.done():
            current_processing_task.cancel()
        is_speaking = False

        # Envoie le contexte de l'appel via LiveKit data channel
        ctx_payload = json.dumps({
            "nom_appelant": call_ctx.nom_appelant,
            "numero": call_ctx.numero,
            "motif": call_ctx.motif,
            "urgence": call_ctx.urgence,
            "resume_demande": call_ctx.resume_demande,
            "service": service,
            "etat": call_ctx.etat,
        }, ensure_ascii=False).encode("utf-8")

        try:
            # room.local_participant.publish_data() pour envoyer aux autres participants
            await room.local_participant.publish_data(
                data=ctx_payload,
                kind=rtc.DataPacketKind.KIND_RELIABLE,
                topic="call_context",
            )
            logger.info(f"[Transfert] Contexte envoyé à la room (service={service})")
        except Exception as e:
            logger.error(f"[Transfert] Erreur envoi contexte: {e}")

        # Log final de l'appel
        logger.info(
            f"[Appel] TRANSFERT vers {service} | "
            f"appelant={call_ctx.nom_appelant or '?'} | "
            f"motif={call_ctx.motif or '?'} | "
            f"messages={len(call_ctx.messages)}"
        )

        # Phase 6+7 — Sauvegarde base + webhook n8n
        await _finalize_call(statut="transfere", transfere=True)

        # L'agent quitte la room après un court délai (laisse le TTS finir)
        await asyncio.sleep(1)
        logger.info("[Transfert] Agent quitte la room. Le collaborateur peut rejoindre.")
        await room.disconnect()

    # --- Pipeline de sortie : Piper TTS -> LiveKit ---
    tts_source = rtc.AudioSource(
        sample_rate=TTS_SAMPLE_RATE,
        num_channels=1,
        queue_size_ms=2000,
    )
    tts_track = rtc.LocalAudioTrack.create_audio_track("agent_voice", tts_source)

    # Barge-in state
    is_speaking = False
    current_tts_task: asyncio.Task | None = None
    tts_stop_event: asyncio.Event | None = None
    current_processing_task: asyncio.Task | None = None  # STT+LLM en cours

    async def trigger_barge_in() -> None:
        """
        Étape 18 — Interrompt tout ce qui est en cours si l'appelant parle :
        la synthèse/lecture TTS ET la requête LLM en cours (si elle n'a pas
        encore abouti). Évite l'empilement de plusieurs réponses.
        """
        nonlocal is_speaking, current_tts_task, tts_stop_event, current_processing_task
        # 1) Coupe le TTS en cours
        if is_speaking and tts_stop_event is not None and not tts_stop_event.is_set():
            logger.info("[Barge-in] Caller spoke during agent reply — interrupting TTS.")
            tts_stop_event.set()
            tts_source.clear_queue()
            if current_tts_task is not None and not current_tts_task.done():
                current_tts_task.cancel()
        # 2) Coupe une requête LLM/STT en cours (pas encore de TTS lancé)
        elif current_processing_task is not None and not current_processing_task.done():
            logger.info("[Barge-in] Caller spoke during processing — cancelling in-flight request.")
            current_processing_task.cancel()

    async def process_utterance(utterance_samples: np.ndarray) -> None:
        """Pipeline complet sur un énoncé détecté par le VAD : STT → LLM → TTS."""
        nonlocal is_speaking, current_tts_task, tts_stop_event, current_processing_task

        if utterance_samples.size == 0:
            return

        # Prépare un AudioBuffer jetable pour la transcription
        buf = AudioBuffer(sample_rate=STT_SAMPLE_RATE)
        buf.buffer = utterance_samples

        logger.info(f"[VAD] Processing utterance ({len(utterance_samples)} samples, "
                    f"~{len(utterance_samples)/STT_SAMPLE_RATE:.1f}s)...")
        try:
            user_text = await transcribe_audio(buf)
        except asyncio.CancelledError:
            logger.info("[VAD] Transcription cancelled (barge-in).")
            return
        if not user_text:
            logger.info("[VAD] Empty transcription, skipping.")
            return

        logger.info(f"User: {user_text}")
        chat_history.append({"role": "user", "content": user_text})

        try:
            agent_response, fn_result = await get_agent_turn(user_text, chat_history, call_ctx)
        except asyncio.CancelledError:
            logger.info("[VAD] LLM request cancelled (barge-in).")
            # Retire le message utilisateur qu'on ne pourra pas répondre
            if chat_history and chat_history[-1].get("role") == "user":
                chat_history.pop()
            return
        logger.info(f"Agent: {agent_response}")
        chat_history.append({"role": "assistant", "content": agent_response})

        # Phase 5 (hook) — si l'agent a demandé un transfert, on l'exécute ici
        if isinstance(fn_result, dict) and fn_result.get("service") and call_ctx.transfer_effectue:
            await _handle_transfer(fn_result["service"])

        # Lancement de la synthèse + lecture (interruptible)
        tts_stop_event = asyncio.Event()
        is_speaking = True
        current_processing_task = None  # le processing est fini, place au TTS
        current_tts_task = asyncio.create_task(
            synthesize_and_play(agent_response, tts_source, tts_stop_event)
        )

        async def _on_tts_done(t: asyncio.Task):
            nonlocal is_speaking
            is_speaking = False
            try:
                await t
            except asyncio.CancelledError:
                pass

        current_tts_task.add_done_callback(
            lambda t: asyncio.create_task(_on_tts_done(t))
        )

    async def consume_audio_track(track: rtc.Track, participant: rtc.RemoteParticipant) -> None:
        """
        Consomme l'audio entrant avec un VAD par énergie (RMS) :
        - état IDLE → SPEAKING quand RMS > VAD_START_RMS
        - état SPEAKING → accumule ; si RMS < VAD_STOP_RMS pendant
          VAD_SILENCE_MS → termine l'énoncé et lance le pipeline
        Ignore les bruits courts (< VAD_MIN_SPEECH_MS).
        """
        nonlocal current_processing_task, current_tts_task, tts_stop_event
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return

        logger.info(f"[Audio] Subscribed to audio from {participant.identity}")
        stream = rtc.AudioStream.from_track(
            track=track,
            sample_rate=STT_SAMPLE_RATE,
            num_channels=1,
        )

        # État du VAD
        is_speaking_now = False
        utterance: list[np.ndarray] = []          # blocs int16 accumulés
        silence_ms = 0
        speech_ms = 0
        max_samples = STT_SAMPLE_RATE * VAD_MAX_UTTERANCE_S

        try:
            async for audio_event in stream:
                frame = audio_event.frame

                # --- Barge-in : on capte de l'audio pendant la réponse de l'agent ---
                if is_speaking:
                    await trigger_barge_in()

                samples = np.frombuffer(frame.data, dtype=np.int16)
                energy = _rms(samples)

                if not is_speaking_now:
                    # En attente de début de parole
                    if energy >= VAD_START_RMS:
                        is_speaking_now = True
                        silence_ms = 0
                        speech_ms = frame.samples_per_channel * 1000 // STT_SAMPLE_RATE
                        utterance.append(samples)
                        logger.debug(f"[VAD] Speech start (rms={energy:.0f})")
                else:
                    # En parole : on accumule tout (parole + silence intermédiaire)
                    utterance.append(samples)
                    frame_ms = frame.samples_per_channel * 1000 // STT_SAMPLE_RATE
                    speech_ms += frame_ms

                    if energy < VAD_STOP_RMS:
                        silence_ms += frame_ms
                        # Énoncé terminé si silence prolongé OU tronqué par limite max
                        if silence_ms >= VAD_SILENCE_MS:
                            if speech_ms - silence_ms >= VAD_MIN_SPEECH_MS:
                                logger.info(
                                    f"[VAD] Speech end (speech={speech_ms - silence_ms}ms, "
                                    f"silence={silence_ms}ms)"
                                )
                                concat = np.concatenate(utterance)
                                # Tronque à max_samples par sécurité
                                if concat.size > max_samples:
                                    concat = concat[:max_samples]
                                # Annule tout processing/TTS en cours avant de lancer le nouveau
                                if current_processing_task and not current_processing_task.done():
                                    current_processing_task.cancel()
                                if current_tts_task and not current_tts_task.done():
                                    tts_stop_event and tts_stop_event.set()
                                    current_tts_task.cancel()
                                tts_source.clear_queue()
                                # Lance le pipeline (TTS/LLM peuvent être interrompus par barge-in)
                                current_processing_task = asyncio.create_task(process_utterance(concat))
                            else:
                                logger.debug(f"[VAD] Discarded short burst ({speech_ms - silence_ms}ms)")
                            # Reset état
                            is_speaking_now = False
                            utterance = []
                            silence_ms = 0
                            speech_ms = 0
                    else:
                        silence_ms = 0  # la parole a repris, reset silence

                logger.debug(
                    f"[Audio] Frame: {frame.sample_rate}Hz, rms={energy:.0f}, "
                    f"speaking={is_speaking_now}"
                )
        finally:
            await stream.aclose()

    def start_audio_consumer(track: rtc.Track, participant: rtc.RemoteParticipant) -> None:
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return
        task = asyncio.create_task(consume_audio_track(track, participant))
        active_audio_tasks.add(task)
        task.add_done_callback(active_audio_tasks.discard)

    @room.on("participant_connected")
    def on_participant_connected(participant: rtc.RemoteParticipant):
        logger.info(f"Appelant connecté : {participant.identity}")

    @room.on("track_subscribed")
    def on_track_subscribed(track: rtc.Track, publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant):
        logger.info(f"Track audio reçu de {participant.identity}")
        start_audio_consumer(track, participant)

    logger.info("Connexion à LiveKit...")
    await room.connect(LIVEKIT_URL, token.to_jwt())
    logger.info(f"Agent connecté à la room '{ROOM_NAME}'. En attente d'un appel...")

    # Publie le track de sortie (voix de l'agent)
    await room.local_participant.publish_track(tts_track)
    logger.info("[TTS] Audio track published (Piper -> LiveKit).")

    # Au cas où un participant audio était déjà présent avant la connexion de l'agent
    for participant in room.remote_participants.values():
        for publication in participant.track_publications.values():
            if publication.kind == rtc.TrackKind.KIND_AUDIO and publication.subscribed and publication.track:
                start_audio_consumer(publication.track, participant)

    try:
        await asyncio.sleep(3600)
    finally:
        # Phase 6+7 — Sauvegarde base + webhook n8n si on quitte sans transfert
        if not call_ctx.transfer_effectue and chat_history:
            await _finalize_call(statut="raccroche")
        for task in list(active_audio_tasks):
            task.cancel()
        if active_audio_tasks:
            await asyncio.gather(*active_audio_tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
