# Scénarios conversationnels — Agent IA Secrétaire

Chaque scénario a un **objectif de collecte de données** et un **état de sortie**.
L'agent progresse via un prompt système structuré (Phase 4) couplé au function-calling (Phase 5).

## États de la machine conversationnelle

```
        ┌─────────┐
        │ ACCUEIL │ ← état initial
        └────┬────┘
             │ (motif détecté)
   ┌─────────┼──────────┬─────────────┐
   ▼         ▼          ▼             ▼
┌──────┐ ┌──────┐ ┌──────────┐ ┌───────────┐
│ FAQ  │ │MSG   │ │QUALIF.   │ │ TRANSFERT │
└──┬───┘ └──┬───┘ └────┬─────┘ └─────┬─────┘
   │        │          │             │
   └────────┴──────────┴─────────────┘
                ▼
            ┌────────┐
            │  FIN   │ (appel clôturé)
            └────────┘
```

---

## S1 — Accueil
- **Déclencheur** : début d'appel (état initial).
- **Objectif** : saluer, identifier l'appelant et son motif.
- **Données à collecter** :
  - `nom_appelant` (obligatoire avant tout transfert)
  - `motif` (libre, puis catégorisé)
- **Transitions** :
  - motif = question courante → **S2 (FAQ)**
  - motif = laisser un message → **S3 (Prise de message)**
  - motif = demande complexe / urgence → **S4 (Qualification)** → **S5 (Transfert)**
- **Prompt associé** : « Bonjour, vous êtes bien chez [Entreprise]. Je suis l'assistant virtuel, comment puis-je vous aider ? »

## S2 — FAQ (consultation)
- **Déclencheur** : motif ≈ horaires, adresse, tarifs, infos générales.
- **Objectif** : répondre via la base de connaissances.
- **Fonction** : `consulter_faq(question)` → renvoie la meilleure réponse.
- **Sortie** : si la réponse satisfait → FIN ; sinon → **S4 (Qualification)**.

## S3 — Prise de message
- **Déclencheur** : l'appelant veut laisser un message / rappel.
- **Données** : `nom`, `numéro`, `contenu_message`, `urgence` (basse/normale/haute).
- **Fonction** : `enregistrer_message(...)`.
- **Sortie** : message confirmé → FIN.

## S4 — Qualification de la demande
- **Déclencheur** : motif non résolu par FAQ, ou demande explicite d'un humain.
- **Objectif** : clarifier la demande pour orienter vers le bon service.
- **Données** : `resume_demande`, `service_cible` (commercial/technique/administratif), `urgence`.
- **Fonction** : `qualifier_demande(...)`.
- **Sortie** : → **S5 (Transfert)**.

## S5 — Transfert vers un humain
- **Déclencheur** : qualification terminée ou demande explicite.
- **Objectif** : annoncer le transfert, prévenir le collaborateur.
- **Fonction** : `transferer_appel(service)`.
- **Message vocal** : « Je vous mets en relation avec un collaborateur, merci de patienter. »
- **Sortie** : second participant rejoint la room (Phase 5) → FIN.

---

## Extraction automatique (machine d'état légère)

À chaque tour, le LLM doit renvoyer un JSON structuré :

```json
{
  "reponse": "Texte que l'agent dit à voix haute.",
  "etat": "ACCUEIL | FAQ | MESSAGE | QUALIFICATION | TRANSFERT | FIN",
  "fonction": {
    "nom": "consulter_faq | enregistrer_message | qualifier_demande | transferer_appel | null",
    "args": { ... }
  },
  "donnees_collectees": {
    "nom_appelant": "string ou null",
    "numero": "string ou null",
    "motif": "string ou null",
    "urgence": "basse | normale | haute | null"
  }
}
```

Le code Python exécute la fonction demandée et fait progresser la machine d'état.
