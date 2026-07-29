"""
Littératie IA (AI Act art. 4) — calcul de l'état d'attestation.

L'obligation est de moyens : c'est la TRACE qui compte. Une attestation doit donc
cesser d'être valable dans deux cas distincts — le temps qui passe (rappel annuel)
et un changement de fond du contenu (nouvelle version). Ces deux règles sont
testées séparément parce qu'elles répondent à deux risques différents.
"""
from datetime import datetime, timedelta, timezone

from services import ai_literacy as al

NOW = datetime(2026, 7, 28, tzinfo=timezone.utc)


def _profile(ack_at=None, version=None):
    return {"ai_literacy_ack_at": ack_at, "ai_literacy_version": version}


def test_never_acknowledged():
    st = al.status(_profile(), now=NOW)
    assert st["state"] == al.NEVER
    assert st["ok"] is False
    assert st["ack_at"] is None


def test_fresh_acknowledgement_is_ok():
    st = al.status(_profile((NOW - timedelta(days=30)).isoformat(), al.VERSION), now=NOW)
    assert st["state"] == al.OK
    assert st["ok"] is True


def test_expires_after_the_validity_window():
    st = al.status(
        _profile((NOW - timedelta(days=al.VALIDITY_DAYS + 1)).isoformat(), al.VERSION), now=NOW
    )
    assert st["state"] == al.EXPIRED
    assert st["ok"] is False


def test_a_content_change_invalidates_immediately():
    # Attestation d'hier, mais sur une version antérieure du contenu : le rappel
    # annuel ne doit pas la maintenir valable.
    st = al.status(_profile((NOW - timedelta(days=1)).isoformat(), "0.9"), now=NOW)
    assert st["state"] == al.OUTDATED
    assert st["ok"] is False


def test_naive_timestamp_does_not_crash():
    # Postgres peut renvoyer un horodatage sans fuseau selon le driver : le
    # comparer à un datetime aware lèverait TypeError et casserait l'écran.
    st = al.status(_profile("2026-07-01T10:00:00", al.VERSION), now=NOW)
    assert st["state"] == al.OK


def test_garbage_timestamp_is_treated_as_never():
    st = al.status(_profile("pas une date", al.VERSION), now=NOW)
    assert st["state"] == al.NEVER


def test_due_date_is_derived_from_the_acknowledgement():
    ack = NOW - timedelta(days=100)
    st = al.status(_profile(ack.isoformat(), al.VERSION), now=NOW)
    assert st["due_at"] == (ack + timedelta(days=al.VALIDITY_DAYS)).isoformat()
