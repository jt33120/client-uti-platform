"""
Littératie IA (AI Act, art. 4) — état d'attestation par utilisateur.

L'art. 4 impose de garantir un niveau suffisant de maîtrise de l'IA chez les
personnes qui opèrent le système. C'est une obligation de MOYENS : elle ne se
démontre pas par un diplôme mais par une trace — qui a été sensibilisé, à quoi,
et quand.

Ce module ne porte que la RÈGLE (version courante, durée de validité, calcul de
l'état). Le contenu de la sensibilisation vit côté front
(`components/AiLiteracyModal.jsx`) : c'est du texte destiné à être lu, pas de la
donnée.

Règle de péremption, volontairement double :
  • une attestation vaut 12 mois (rappel annuel) ;
  • un changement de VERSION périme immédiatement les attestations antérieures,
    afin qu'une évolution substantielle du système (nouveau modèle, nouveau
    critère, nouvelle façon de lire le score) soit réellement réexpliquée.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

# Bump à chaque évolution SUBSTANTIELLE du contenu de sensibilisation.
# Un simple correctif de formulation ne justifie pas de repasser tout le monde
# en « à refaire » : ne l'incrémenter que si le fond change.
VERSION = "1.0"

VALIDITY_DAYS = 365

# États possibles, du plus au moins urgent.
NEVER = "never"        # jamais attesté
OUTDATED = "outdated"  # attesté sur une version antérieure du contenu
EXPIRED = "expired"    # attesté sur la bonne version, mais il y a plus de 12 mois
OK = "ok"


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    # Une valeur naïve serait comparée à un aware plus bas → TypeError.
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def status(profile: dict, now: Optional[datetime] = None) -> dict:
    """État de littératie IA d'un profil. Ne lève jamais.

    Retourne `state` (never/outdated/expired/ok), `ok` (bool), la date
    d'attestation, la version attestée et la date de prochaine échéance.
    """
    now = now or datetime.now(timezone.utc)
    ack_at = _parse((profile or {}).get("ai_literacy_ack_at"))
    version = (profile or {}).get("ai_literacy_version") or None

    if not ack_at:
        state = NEVER
    elif version != VERSION:
        state = OUTDATED
    elif now - ack_at > timedelta(days=VALIDITY_DAYS):
        state = EXPIRED
    else:
        state = OK

    return {
        "state": state,
        "ok": state == OK,
        "ack_at": ack_at.isoformat() if ack_at else None,
        "acked_version": version,
        "current_version": VERSION,
        "validity_days": VALIDITY_DAYS,
        "due_at": (ack_at + timedelta(days=VALIDITY_DAYS)).isoformat() if ack_at else None,
    }
