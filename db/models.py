"""
Phase 7 — Persistance SQLite des appels.

Schéma : table `appels` (id, date, participant, duree_approx, transcription,
resumé, motif, urgence, statut, transféré, service, metadata_json).

Export CSV via `python db/models.py export`.
"""
from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime
from pathlib import Path

_DB_PATH = Path(__file__).resolve().parent / "agent.db"


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Crée la table si elle n'existe pas."""
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS appels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            participant TEXT DEFAULT 'caller',
            duree_approx_sec INTEGER DEFAULT 0,
            transcription_complete TEXT DEFAULT '',
            resume TEXT DEFAULT '',
            motif TEXT DEFAULT '',
            urgence TEXT DEFAULT NULL,
            statut TEXT DEFAULT 'en_cours',
            transfere INTEGER DEFAULT 0,
            service TEXT DEFAULT NULL,
            metadata_json TEXT DEFAULT '{}'
        )
    """)
    conn.commit()
    conn.close()


def save_appel(
    participant: str = "caller",
    duree_approx_sec: int = 0,
    transcription: str = "",
    resume: str = "",
    motif: str = "",
    urgence: str | None = None,
    statut: str = "termine",
    transfere: bool = False,
    service: str | None = None,
    metadata: dict | None = None,
) -> int:
    """Enregistre un appel terminé. Renvoie l'ID inséré."""
    conn = _get_conn()
    cursor = conn.execute(
        """
        INSERT INTO appels (date, participant, duree_approx_sec, transcription_complete,
                            resume, motif, urgence, statut, transfere, service, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now().isoformat(),
            participant,
            duree_approx_sec,
            transcription,
            resume,
            motif,
            urgence,
            statut,
            int(transfere),
            service,
            json.dumps(metadata or {}, ensure_ascii=False),
        ),
    )
    call_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return call_id


def list_appels(since: str | None = None) -> list[dict]:
    """Renvoie tous les appels, optionnellement filtrés par date (ISO)."""
    conn = _get_conn()
    if since:
        rows = conn.execute(
            "SELECT * FROM appels WHERE date >= ? ORDER BY date DESC", (since,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM appels ORDER BY date DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def export_csv(output_path: str | Path | None = None, since: str | None = None) -> str:
    """
    Exporte les appels en CSV. Renvoie le chemin du fichier.
    """
    if output_path is None:
        output_path = Path(__file__).resolve().parent / f"appels_{datetime.now():%Y%m%d}.csv"
    output_path = Path(output_path)

    appels = list_appels(since=since)
    if not appels:
        print("Aucun appel à exporter.")
        return str(output_path)

    fieldnames = [
        "id", "date", "participant", "duree_approx_sec", "motif",
        "urgence", "statut", "transfere", "service", "resume",
    ]
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(appels)

    print(f"Exporté {len(appels)} appels → {output_path}")
    return str(output_path)


# Auto-init à l'import
init_db()

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "export":
        since = sys.argv[2] if len(sys.argv) > 2 else None
        export_csv(since=since)
    else:
        appels = list_appels()
        print(f"📋 {len(appels)} appel(s) enregistré(s)")
        for a in appels[:10]:
            print(f"  #{a['id']} {a['date']} | {a['motif'] or '?'} | {a['statut']} | transféré={bool(a['transfere'])}")
