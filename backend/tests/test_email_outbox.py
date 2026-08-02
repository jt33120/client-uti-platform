"""
File d'envoi des emails — règle de réessai.

C'est la partie où une erreur se paie en emails perdus (un lien de
réinitialisation qui n'arrive jamais, une invitation partenaire disparue), donc
elle est vérifiée sans base.

Deux propriétés comptent :
  • un échec ne perd jamais l'email — la ligne repart en file avec un délai ;
  • l'insistance est bornée — au-delà de MAX_ATTEMPTS la ligne est abandonnée,
    mais reste consultable avec sa dernière erreur, pour pouvoir répondre à
    « pourquoi n'ai-je rien reçu ? ».
"""
from datetime import datetime, timedelta, timezone

from services import email_outbox as ob

NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)


def test_first_failure_requeues_quickly():
    patch = ob.plan_retry(0, "SMTP: timeout", NOW)
    assert patch["status"] == "queued"
    assert patch["attempts"] == 1
    # Un incident bref doit être rattrapé vite.
    assert patch["next_attempt_at"] == (NOW + timedelta(minutes=ob.BACKOFF_MINUTES[0])).isoformat()


def test_delay_grows_with_attempts():
    delays = []
    for n in range(ob.MAX_ATTEMPTS - 1):
        patch = ob.plan_retry(n, "boom", NOW)
        planned = datetime.fromisoformat(patch["next_attempt_at"])
        delays.append((planned - NOW).total_seconds())
    assert delays == sorted(delays), "le délai doit croître, jamais décroître"
    assert delays[0] < delays[-1]


def test_gives_up_after_max_attempts():
    patch = ob.plan_retry(ob.MAX_ATTEMPTS - 1, "SMTP: 550 mailbox unavailable", NOW)
    assert patch["status"] == "dead"
    assert patch["attempts"] == ob.MAX_ATTEMPTS
    assert "next_attempt_at" not in patch, "une ligne abandonnée ne se replanifie pas"


def test_abandoned_row_keeps_its_error():
    # Sans l'erreur conservée, impossible de diagnostiquer après coup.
    patch = ob.plan_retry(ob.MAX_ATTEMPTS, "SMTP: 550 no such user", NOW)
    assert patch["status"] == "dead"
    assert patch["last_error"] == "SMTP: 550 no such user"


def test_claim_is_always_released():
    # Une ligne qui échoue doit relâcher sa réservation, sinon elle resterait
    # bloquée en « sending » jusqu'à la reprise des lignes coincées.
    for n in (0, 2, ob.MAX_ATTEMPTS):
        assert ob.plan_retry(n, "err", NOW)["claimed_at"] is None


def test_backoff_table_covers_max_attempts():
    # Un décalage entre les deux constantes ferait sortir de la liste (IndexError
    # au pire, plafonnement silencieux au mieux) : on vérifie la cohérence.
    assert len(ob.BACKOFF_MINUTES) >= ob.MAX_ATTEMPTS - 1


def test_total_retry_window_is_meaningful():
    # Le but est de traverser une panne SMTP de plusieurs heures, pas d'insister
    # trois minutes puis d'abandonner.
    total = sum(ob.BACKOFF_MINUTES[: ob.MAX_ATTEMPTS - 1])
    assert total >= 240, f"fenêtre de réessai trop courte : {total} min"
