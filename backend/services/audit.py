"""
Journal d'audit du pipeline de matching (AI Act Art. 12 — journalisation, Art. 19).

Append-only, best-effort : l'écriture d'un log ne doit jamais casser un scoring.
Aucune donnée personnelle en clair (CV, nom) — on ne stocke qu'un hash des
features et des métadonnées techniques. La table `audit_log` est en RLS deny-all
(accès via backend service_role uniquement) — voir
supabase_migration_audit_log.sql.
"""
import json
import hashlib
import uuid
from typing import Optional
from services.postgrest_client import db


def new_run_id() -> str:
    """Identifiant corrélant toutes les lignes d'un même scoring."""
    return str(uuid.uuid4())


def features_hash(features: dict) -> str:
    """Empreinte stable des features (pas de PII en clair dans les logs)."""
    blob = json.dumps(features or {}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def log_event(
    event_type: str,
    run_id: str,
    *,
    ao_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    model_version: Optional[str] = None,
    grid_version: Optional[str] = None,
    input_hash: Optional[str] = None,
    payload: Optional[dict] = None,
    severity: str = "info",
) -> None:
    """Écrit une ligne d'audit. Best-effort : log l'échec mais ne lève jamais."""
    try:
        db.table("audit_log").insert({
            "run_id": run_id,
            "ao_id": ao_id,
            "event_type": event_type,
            "actor_id": actor_id,
            "model_version": model_version,
            "grid_version": grid_version,
            "input_hash": input_hash,
            "payload": payload,
            "severity": severity,
        }).execute()
    except Exception as e:  # noqa: BLE001
        # La table peut ne pas encore exister (migration non appliquée) — on ne
        # bloque jamais le scoring pour autant.
        print(f"[AUDIT] event '{event_type}' non journalisé: {e}")
