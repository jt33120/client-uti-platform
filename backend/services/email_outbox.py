"""
File d'attente d'envoi des emails.

L'appelant DÉPOSE (`enqueue`), le planificateur DÉPILE (`process_outbox`).

Ce que ça règle, et qui ne l'était pas :

  • **Reprise sur échec.** Un hoquet SMTP de deux secondes perdait l'email
    définitivement. Une ligne en échec est replanifiée à intervalle croissant
    et retentée jusqu'à `MAX_ATTEMPTS`.
  • **Blocage de la requête HTTP.** L'envoi se faisait dans le fil de la
    requête : l'utilisateur attendait la poignée de main SMTP, jusqu'à 15 s.
  • **Connexions SMTP.** Le lot entier passe désormais par UNE session.
  • **Traçabilité.** 10 des 12 points d'envoi n'étaient journalisés nulle part.
    Tout passant par la file, le journal devient universel par construction.

Mono-worker assumé : le planificateur est une boucle `asyncio` dans un uvicorn
à un seul worker (cf. services/scheduler.py). Pas de coordination à gérer pour
dépiler — mais il faut savoir reprendre les lignes laissées en « sending » par
un redémarrage, d'où `_recover_stuck`.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from services.supabase_client import supabase
from services.error_log import record as _record_err
from services import email as email_service

#: Lignes traitées par tick. Le planificateur tourne toutes les heures ; le lot
#: doit rester envoyable bien en dessous de cette fenêtre.
BATCH = 100

#: Au-delà, la ligne passe en « dead » et cesse d'être retentée. Avec la
#: progression ci-dessous, cela couvre environ 8 heures de panne SMTP.
MAX_ATTEMPTS = 6

#: Délai avant nouvelle tentative, en minutes, indexé sur le nombre d'échecs.
#: Croissant : un incident bref est rattrapé vite, une panne longue n'inonde
#: pas le serveur de tentatives inutiles.
BACKOFF_MINUTES = [1, 5, 15, 60, 180, 360]

#: Une ligne « sending » plus vieille que ça vient forcément d'un processus mort
#: (le lot entier tient en quelques minutes) : on la remet en file.
STUCK_AFTER_MINUTES = 15


def enqueue(
    *,
    to_email: str,
    subject: str,
    html: str,
    category: str,
    text: Optional[str] = None,
    to_name: Optional[str] = None,
    reply_to: Optional[str] = None,
    template_key: Optional[str] = None,
    context: Optional[dict] = None,
    ao_id: Optional[str] = None,
    recipient_id: Optional[str] = None,
    created_by: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> Optional[dict]:
    """Dépose un email dans la file. Ne lève jamais.

    `idempotency_key` rend le dépôt rejouable sans doublon : un double clic, un
    tick rejoué ou un redémarrage au mauvais moment produisent la même ligne.
    Le conflit d'unicité est donc un SUCCÈS silencieux, pas une erreur.
    """
    if not to_email:
        return None
    row = {
        "to_email": to_email,
        "to_name": to_name,
        "reply_to": reply_to,
        "subject": subject,
        "html": html,
        "text": text,
        "category": category,
        "template_key": template_key,
        "context": context,
        "ao_id": ao_id,
        "recipient_id": recipient_id,
        "created_by": created_by,
        "idempotency_key": idempotency_key,
    }
    try:
        created = supabase.table("email_outbox").insert(row).execute().data
        return (created or [None])[0]
    except Exception as e:  # noqa: BLE001
        msg = str(e).lower()
        if idempotency_key and ("duplicate" in msg or "unique" in msg or "23505" in msg):
            # Déjà en file : c'est exactement le comportement voulu.
            return None
        _record_err("email", f"Dépôt en file impossible pour {to_email}", exc=e)
        return None


def _recover_stuck(now: datetime) -> int:
    """Remet en file les lignes laissées en « sending » par un processus mort."""
    cutoff = (now - timedelta(minutes=STUCK_AFTER_MINUTES)).isoformat()
    try:
        rows = supabase.table("email_outbox").update(
            {"status": "queued", "claimed_at": None}
        ).eq("status", "sending").lt("claimed_at", cutoff).execute().data or []
        if rows:
            _record_err(
                "email",
                f"{len(rows)} email(s) repris après un arrêt en cours d'envoi",
                level="warning",
            )
        return len(rows)
    except Exception:  # noqa: BLE001 - la reprise ne doit pas bloquer le tick
        return 0


def _claim(now: datetime) -> list[dict]:
    """Réserve un lot de lignes prêtes à partir."""
    try:
        ready = supabase.table("email_outbox").select("*").eq("status", "queued").lte(
            "next_attempt_at", now.isoformat()
        ).order("next_attempt_at").limit(BATCH).execute().data or []
    except Exception as e:  # noqa: BLE001
        _record_err("email", "Lecture de la file impossible", exc=e)
        return []
    if not ready:
        return []

    ids = [r["id"] for r in ready]
    try:
        supabase.table("email_outbox").update(
            {"status": "sending", "claimed_at": now.isoformat()}
        ).in_("id", ids).execute()
    except Exception as e:  # noqa: BLE001
        # Sans réservation, on renonce à ce tick plutôt que de risquer un
        # double envoi : les lignes restent « queued » et repartiront.
        _record_err("email", "Réservation du lot impossible", exc=e)
        return []
    return ready


def _mark_sent(row_id: str, now: datetime) -> None:
    supabase.table("email_outbox").update({
        "status": "sent", "sent_at": now.isoformat(), "last_error": None,
    }).eq("id", row_id).execute()


def plan_retry(attempts_before: int, err: str, now: datetime) -> dict:
    """État suivant d'une ligne qui vient d'échouer. Fonction pure, testable.

    Séparée de l'écriture en base pour que la règle de réessai — la partie où
    une erreur se paie en emails perdus — soit vérifiable sans base.
    """
    attempts = int(attempts_before or 0) + 1
    if attempts >= MAX_ATTEMPTS:
        # Abandon : la ligne reste consultable dans le journal avec sa dernière
        # erreur. On ne supprime jamais — c'est ce qui permet de répondre à
        # « pourquoi n'ai-je rien reçu ? ».
        return {"status": "dead", "attempts": attempts, "last_error": err, "claimed_at": None}
    delay = BACKOFF_MINUTES[min(attempts - 1, len(BACKOFF_MINUTES) - 1)]
    return {
        "status": "queued",
        "attempts": attempts,
        "last_error": err,
        "claimed_at": None,
        "next_attempt_at": (now + timedelta(minutes=delay)).isoformat(),
    }


def _mark_failed(row: dict, err: str, now: datetime) -> None:
    patch = plan_retry(row.get("attempts"), err, now)
    supabase.table("email_outbox").update(patch).eq("id", row["id"]).execute()


def process_outbox(now: Optional[datetime] = None) -> dict:
    """Dépile un lot. Ne lève jamais — appelé depuis le planificateur."""
    now = now or datetime.now(timezone.utc)

    cfg_err = email_service.config_error()
    if cfg_err:
        # SMTP non configuré : inutile de réserver des lignes pour les faire
        # échouer et consommer leurs tentatives.
        return {"status": "disabled", "reason": cfg_err, "sent": 0, "failed": 0}

    recovered = _recover_stuck(now)
    rows = _claim(now)
    if not rows:
        return {"status": "ok", "sent": 0, "failed": 0, "recovered": recovered}

    sent = failed = 0
    # Une seule session pour tout le lot — c'est l'intérêt principal du batch.
    with email_service.SmtpSession() as session:
        for row in rows:
            try:
                msg = email_service.build_message(
                    row["to_email"], row["subject"], row["html"],
                    text=row.get("text"),
                    reply_to=row.get("reply_to"),
                    to_name=row.get("to_name"),
                )
                ok, err = session.send(msg)
            except Exception as e:  # noqa: BLE001 - message malformé, encodage…
                ok, err = False, str(e)

            try:
                if ok:
                    _mark_sent(row["id"], now)
                    sent += 1
                else:
                    _mark_failed(row, err or "erreur inconnue", now)
                    failed += 1
            except Exception as e:  # noqa: BLE001
                # L'email est parti mais l'état n'a pas pu être écrit : la ligne
                # sera reprise par `_recover_stuck` et renvoyée. Un doublon vaut
                # mieux qu'un email perdu, mais il faut le savoir.
                _record_err("email", f"État non écrit pour l'envoi {row['id']}", exc=e)

    if failed:
        _record_err("email", f"File d'envoi : {sent} envoyé(s), {failed} en échec", level="warning")
    return {"status": "ok", "sent": sent, "failed": failed, "recovered": recovered}


def stats(now: Optional[datetime] = None) -> dict:
    """Compteurs de la file, pour l'écran d'administration."""
    out: dict = {}
    for status in ("queued", "sending", "sent", "dead"):
        try:
            out[status] = supabase.table("email_outbox").select(
                "id", count="exact"
            ).eq("status", status).limit(1).execute().count
        except Exception:  # noqa: BLE001
            out[status] = None
    return out
