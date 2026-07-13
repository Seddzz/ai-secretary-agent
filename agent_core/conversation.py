"""
Phase 4 — Logique conversationnelle de l'agent secrétaire.

Contient :
- Le chargement de la FAQ (faq.json) + recherche par similarité lexicale.
- Le prompt système structuré qui pilote la machine d'états (voir scenarios.md).
- Les fonctions exposées au LLM : consulter_faq, enregistrer_message,
  qualifier_demande, transferer_appel.
- L'analyseur de réponse structurée JSON (réponse + état + fonction + données).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# FAQ : chargement + recherche par similarité
# ---------------------------------------------------------------------------

_FAQ_PATH = Path(__file__).resolve().parent / "faq.json"
_stopwords = {
    # mots vides FR — ignorés dans la similarité
    "le", "la", "les", "un", "une", "des", "de", "du", "et", "ou", "à", "au",
    "aux", "ce", "cet", "cette", "ces", "mon", "ma", "mes", "votre", "vos",
    "je", "tu", "il", "elle", "nous", "vous", "ils", "elles", "est", "sont",
    "pour", "par", "avec", "sans", "sur", "dans", "que", "qui", "quoi", "comment",
    "quel", "quelle", "quels", "quelles", "the", "a", "an", "of", "to", "is",
    "vos", "notre", "etre", "avoir", "vous", "voudrais", "souhaite", "ai",
    "suis", "mon", "notre",
}

# Normalisation des accents : on garde la correspondance même sans accents
_ACCENT_MAP = str.maketrans("àâäéèêëîïôöùûüç", "aaaeeeeiioouuuc")


def _normalize(token: str) -> str:
    """Minuscules + suppression d'accents."""
    return token.lower().translate(_ACCENT_MAP)


def _tokenize(text: str) -> set[str]:
    """Tokenisation : minuscules, sans accents, mots >= 3 lettres, hors stopwords."""
    tokens = re.findall(r"[a-zàâäéèêëîïôöùûüç]+", text.lower())
    return {_normalize(t) for t in tokens if len(t) >= 3 and _normalize(t) not in _stopwords}


def load_faq(path: Path = _FAQ_PATH) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("entries", [])


_FAQ_ENTRIES: list[dict] = load_faq()


def consulter_faq(question: str, threshold: float = 0.20) -> dict[str, Any]:
    """
    Recherche la meilleure entrée FAQ pour une question utilisateur.

    Score = recouvrement pondéré :
      - tokens de la question qui apparaissent dans (question + mots-clés)
        de l'entrée FAQ, avec un bonus pour les mots-clés explicites.
      - normalisé par le nombre de tokens de la question (pas de pénalité
        pour les références longues, contrairement au Jaccard pur).

    Renvoie {'id', 'question', 'reponse', 'score'} ou {'reponse': None}.
    """
    q_tokens = _tokenize(question)
    if not q_tokens:
        return {"reponse": None, "score": 0.0, "raison": "Question vide."}

    best: dict[str, Any] | None = None
    best_score = 0.0
    for entry in _FAQ_ENTRIES:
        question_tokens = _tokenize(entry["question"])
        keyword_tokens = _tokenize(" ".join(entry.get("mots_cles", [])))
        ref_tokens = question_tokens | keyword_tokens

        # Hits : tokens de la question présents dans la réf.
        hits = q_tokens & ref_tokens
        if not hits:
            continue
        # Bonus : un hit sur un mot-clé explicite compte double
        keyword_hits = hits & keyword_tokens
        score = (len(hits) + len(keyword_hits)) / len(q_tokens)

        if score > best_score:
            best_score = score
            best = entry

    if best is None or best_score < threshold:
        return {
            "reponse": None,
            "score": round(best_score, 3),
            "raison": "Aucune entrée FAQ suffisamment pertinente.",
        }

    return {
        "id": best["id"],
        "question": best["question"],
        "reponse": best["reponse"],
        "score": round(best_score, 3),
    }


# ---------------------------------------------------------------------------
# Contexte d'appel + fonctions métier (les "tools" du LLM)
# ---------------------------------------------------------------------------

@dataclass
class CallContext:
    """Contexte vivant d'un appel : collecte progressive d'informations."""
    nom_appelant: str | None = None
    numero: str | None = None
    motif: str | None = None
    urgence: str | None = None          # "basse" | "normale" | "haute"
    service_cible: str | None = None     # "commercial" | "technique" | "administratif"
    resume_demande: str | None = None
    etat: str = "ACCUEIL"                # machine d'états
    messages: list[dict] = field(default_factory=list)  # messages enregistrés
    transfer_effectue: bool = False
    faq_consultee: list[str] = field(default_factory=list)


def enregistrer_message(ctx: CallContext, contenu: str,
                        urgence: str = "normale") -> dict:
    """Enregistre un message laissé par l'appelant."""
    msg = {
        "nom": ctx.nom_appelant or "Inconnu",
        "numero": ctx.numero or "Non fourni",
        "contenu": contenu,
        "urgence": urgence,
    }
    ctx.messages.append(msg)
    return {"ok": True, "message_id": len(ctx.messages), "message": msg}


def qualifier_demande(ctx: CallContext, resume: str,
                      service: str, urgence: str = "normale") -> dict:
    """Qualifie la demande pour orienter vers le bon service."""
    service = service.lower().strip()
    if service not in {"commercial", "technique", "administratif"}:
        service = "commercial"  # défaut
    ctx.resume_demande = resume
    ctx.service_cible = service
    ctx.urgence = urgence
    ctx.etat = "QUALIFICATION"
    return {
        "ok": True,
        "resume": resume,
        "service": service,
        "urgence": urgence,
    }


def transferer_appel(ctx: CallContext, service: str = "commercial") -> dict:
    """Déclenche le transfert vers un service (Phase 5)."""
    service = service.lower().strip()
    if service not in {"commercial", "technique", "administratif"}:
        service = "commercial"
    ctx.service_cible = service
    ctx.transfer_effectue = True
    ctx.etat = "TRANSFERT"
    return {
        "ok": True,
        "service": service,
        "message": f"Transfert vers le service {service} demandé.",
    }


# Disponibilité des fonctions pour le LLM (documentées dans le prompt)
AVAILABLE_FUNCTIONS = {
    "consulter_faq": consulter_faq,
    "enregistrer_message": enregistrer_message,
    "qualifier_demande": qualifier_demande,
    "transferer_appel": transferer_appel,
}


# ---------------------------------------------------------------------------
# Prompt système structuré (machine d'états via LLM)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """Tu es un SECRÉTAIRE TÉLÉPHONIQUE IA professionnel, courtois et efficace. \
Tu accueilles les appelants pour "Acme Conseil" (entreprise de conseil).

## Règles de conduite
- Réponds TOUJOURS en français, de manière concise (2-3 phrases max à l'oral).
- Sois chaleureux mais professionnel.
- Ne JAMAIS inventer d'informations. Si tu ne sais pas, propose de transférer.
- Récolte le NOM de l'appelant et son MOTIF avant toute action.

## Scénarios / états
1. ACCUEIL — saluer, demander nom + motif
2. FAQ — répondre aux questions courantes (horaires, adresse, tarifs...)
3. MESSAGE — prendre un message complet (nom, n°, contenu, urgence)
4. QUALIFICATION — clarifier une demande complexe pour orienter le bon service
5. TRANSFERT — annoncer la mise en relation avec un collaborateur
6. FIN — clôturer poliment

## Format de réponse OBLIGATOIRE
Tu DOIS répondre UNIQUEMENT avec un objet JSON valide (aucun texte avant/après), \
de cette forme exacte :

{
  "reponse": "Ce que tu dis à l'appelant, à voix haute.",
  "etat": "ACCUEIL|FAQ|MESSAGE|QUALIFICATION|TRANSFERT|FIN",
  "fonction": {
    "nom": "<UNE SEULE valeur parmi: consulter_faq, enregistrer_message, qualifier_demande, transferer_appel, ou null — jamais plusieurs, jamais séparées par |>",
    "args": {}
  },
  "donnees_collectees": {
    "nom_appelant": "string ou null",
    "numero": "string ou null",
    "motif": "string ou null",
    "urgence": "basse|normale|haute|null",
    "service": "commercial|technique|administratif|null",
    "resume_demande": "string ou null"
  }
}

## Quand appeler quelle fonction
IMPORTANT : N'appelle consulter_faq QUE si l'appelant pose une vraie question factuelle 
(horaires, adresse, tarif...). Ne l'appelle JAMAIS pour une simple salutation comme "bonjour".
- consulter_faq : quand l'appelant pose une question courante (horaires, adresse, tarif, etc.). \
Args: {"question": "<la question de l'appelant>"}.
- enregistrer_message : quand l'appelant veut laisser un message/rappel. \
Args: {"contenu": "<texte>", "urgence": "normale"}.
- qualifier_demande : pour une demande complexe nécessitant un humain. \
Args: {"resume": "<résumé>", "service": "commercial|technique|administratif", "urgence": "normale"}.
- transferer_appel : pour transférer vers un collaborateur. \
Args: {"service": "commercial|technique|administratif"}.

Si aucune fonction n'est nécessaire pour ce tour, mets "fonction": {"nom": null}.
IMPORTANT : le champ "nom" ne contient QU'UNE SEULE fonction à la fois, jamais une liste.

## Exemples

Utilisateur: "Bonjour, quels sont vos horaires ?"
{"reponse":"Bonjour ! Nous sommes ouverts du lundi au vendredi de 9h à 18h, et le samedi de 9h à 13h.","etat":"FAQ","fonction":{"nom":"consulter_faq","args":{"question":"horaires d'ouverture"}},"donnees_collectees":{"nom_appelant":null,"numero":null,"motif":"informations horaires","urgence":"basse","service":null,"resume_demande":null}}

Utilisateur: "Je veux parler à un conseiller"
{"reponse":"Bien sûr, je vous mets en relation avec notre service commercial. Merci de patienter un instant.","etat":"TRANSFERT","fonction":{"nom":"transferer_appel","args":{"service":"commercial"}},"donnees_collectees":{"nom_appelant":null,"numero":null,"motif":"parler à un conseiller","urgence":"normale","service":"commercial","resume_demande":null}}
"""


# ---------------------------------------------------------------------------
# Parseur de réponse LLM
# ---------------------------------------------------------------------------

def parse_llm_response(raw: str) -> dict[str, Any]:
    """
    Extrait le JSON de la réponse du LLM (tolérant au texte autour).
    Renvoie un dict avec clés : reponse, etat, fonction, donnees_collectees.
    En cas d'échec, renvoie une réponse de repli.
    """
    # Cherche le premier bloc {...} équilibré
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return {
            "reponse": "Pouvez-vous répéter, je n'ai pas bien compris ?",
            "etat": "ACCUEIL",
            "fonction": {"nom": None},
            "donnees_collectees": {},
        }
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {
            "reponse": "Pouvez-vous reformuler votre demande ?",
            "etat": "ACCUEIL",
            "fonction": {"nom": None},
            "donnees_collectees": {},
        }

    return {
        "reponse": data.get("reponse", "") or "Je n'ai pas de réponse à proposer.",
        "etat": data.get("etat", "ACCUEIL"),
        "fonction": data.get("fonction") or {"nom": None},
        "donnees_collectees": data.get("donnees_collectees") or {},
    }


def apply_extraction(ctx: CallContext, donnees: dict[str, Any]) -> None:
    if not isinstance(donnees, dict):
        return

    def _clean(v):
        if isinstance(v, str) and v.strip().lower() in {"null", "none", ""}:
            return None
        return v

    nom = _clean(donnees.get("nom_appelant"))
    if nom:
        ctx.nom_appelant = nom
    numero = _clean(donnees.get("numero"))
    if numero:
        ctx.numero = numero
    motif = _clean(donnees.get("motif"))
    if motif:
        ctx.motif = motif
    if donnees.get("urgence") in {"basse", "normale", "haute"}:
        ctx.urgence = donnees["urgence"]
    if donnees.get("service") in {"commercial", "technique", "administratif"}:
        ctx.service_cible = donnees["service"]
    resume = _clean(donnees.get("resume_demande"))
    if resume:
        ctx.resume_demande = resume


def execute_function(ctx: CallContext, fonction: dict[str, Any]) -> dict[str, Any]:
    """
    Exécute la fonction demandée par le LLM. Renvoie le résultat (dict)
    ou {'ok': False, 'erreur': ...} si la fonction est inconnue.
    """
    if not isinstance(fonction, dict):
        return {"ok": False, "erreur": "fonction manquante"}
    name = fonction.get("nom")
    if not name or name == "null":
        return {"ok": True, "skipped": True}
    args = fonction.get("args") or {}
    fn = AVAILABLE_FUNCTIONS.get(name)
    if fn is None:
        return {"ok": False, "erreur": f"fonction inconnue: {name}"}
    try:
        # Toutes les fonctions de message/qualification/transfert prennent ctx en 1er arg
        if name == "consulter_faq":
            return fn(args.get("question", ""))
        return fn(ctx, **{k: v for k, v in args.items()})
    except TypeError as e:
        return {"ok": False, "erreur": f"args invalides pour {name}: {e}"}
