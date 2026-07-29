"""
Conformité partenaire — obligation de vigilance (art. L.8222-1 c. trav.).

Les règles testées ici sont celles qui distinguent une mise en conformité réelle
d'un simple classeur de PDF. Trois pièges, tous vérifiés :

  • une attestation détenue mais NON VÉRIFIÉE auprès de l'URSSAF ne purge pas
    l'obligation — elle ne doit donc jamais compter comme valide ;
  • la validité court depuis l'ÉMISSION de la pièce, pas depuis son dépôt ;
  • la liste des salariés étrangers relève d'un régime distinct (art. L.8254-1),
    sans périodicité : son absence ne doit pas mettre un partenaire en défaut.
"""
from datetime import date, timedelta

from services import partner_compliance as pc

TODAY = date(2026, 7, 29)


def _doc(doc_type, issued_days_ago, checked=False):
    return {
        "id": f"d-{doc_type}",
        "doc_type": doc_type,
        "issued_at": (TODAY - timedelta(days=issued_days_ago)).isoformat(),
        "authenticity_checked_at": "2026-07-01T10:00:00+00:00" if checked else None,
    }


def test_vigilance_held_but_unverified_is_not_valid():
    # Le cœur du sujet : un PDF téléversé ne suffit pas.
    st = pc.doc_status(_doc(pc.VIGILANCE, 10, checked=False), TODAY)
    assert st["state"] == pc.UNVERIFIED


def test_vigilance_verified_and_recent_is_valid():
    st = pc.doc_status(_doc(pc.VIGILANCE, 10, checked=True), TODAY)
    assert st["state"] == pc.VALID


def test_validity_runs_from_issuance_not_from_upload():
    # Émise il y a 5 mois et demi : il reste moins de 30 jours → alerte, même si
    # elle vient d'être déposée à l'instant.
    st = pc.doc_status(_doc(pc.VIGILANCE, 165, checked=True), TODAY)
    assert st["state"] == pc.EXPIRING
    assert 0 <= st["days_left"] <= pc.EXPIRY_WARNING_DAYS


def test_vigilance_past_six_months_is_expired():
    st = pc.doc_status(_doc(pc.VIGILANCE, 200, checked=True), TODAY)
    assert st["state"] == pc.EXPIRED


def test_expired_wins_over_unverified():
    # Une pièce à la fois périmée et non vérifiée doit remonter « périmée » :
    # c'est l'action la plus urgente (redemander la pièce, pas la vérifier).
    st = pc.doc_status(_doc(pc.VIGILANCE, 200, checked=False), TODAY)
    assert st["state"] == pc.EXPIRED


def test_missing_foreign_workers_list_does_not_fail_the_partner():
    # Régime distinct (L.8254-1), exigible à la conclusion et seulement si le
    # partenaire emploie des salariés soumis à autorisation de travail.
    docs = [_doc(pc.VIGILANCE, 10, checked=True), _doc(pc.IMMATRICULATION, 10)]
    st = pc.partner_status(docs, TODAY)
    assert st["by_type"][pc.SALARIES_ETRANGERS]["state"] == pc.MISSING
    assert st["ok"] is True, "une pièce non requise ne doit pas mettre en défaut"


def test_missing_vigilance_fails_the_partner():
    st = pc.partner_status([_doc(pc.IMMATRICULATION, 10)], TODAY)
    assert st["overall"] == pc.MISSING
    assert st["ok"] is False


def test_overall_reflects_the_worst_required_document():
    docs = [_doc(pc.VIGILANCE, 200, checked=True), _doc(pc.IMMATRICULATION, 10)]
    st = pc.partner_status(docs, TODAY)
    assert st["overall"] == pc.EXPIRED


def test_most_recent_document_of_a_type_wins():
    # Une attestation périmée ne doit pas empoisonner l'état quand une plus
    # récente a été déposée — c'est le cas nominal du renouvellement semestriel.
    old = _doc(pc.VIGILANCE, 200, checked=True)
    fresh = {**_doc(pc.VIGILANCE, 5, checked=True), "id": "fresh"}
    st = pc.partner_status([old, fresh, _doc(pc.IMMATRICULATION, 10)], TODAY)
    assert st["by_type"][pc.VIGILANCE]["doc"]["id"] == "fresh"
    assert st["by_type"][pc.VIGILANCE]["state"] == pc.VALID
    assert st["overall"] == pc.VALID


def test_immatriculation_needs_no_authenticity_check():
    # Seule l'attestation URSSAF porte une exigence d'authenticité.
    st = pc.doc_status(_doc(pc.IMMATRICULATION, 10, checked=False), TODAY)
    assert st["state"] == pc.VALID
    assert st["advisory"] is True, "les 3 mois sont un usage, pas une péremption légale"


def test_no_documents_at_all():
    st = pc.partner_status([], TODAY)
    assert st["overall"] == pc.MISSING
    assert st["ok"] is False


def test_garbage_issue_date_is_survivable():
    st = pc.doc_status({"doc_type": pc.VIGILANCE, "issued_at": "n/a",
                        "authenticity_checked_at": None}, TODAY)
    # Sans date exploitable, pas d'échéance calculable : c'est le défaut
    # d'authenticité qui prime, et l'écran reste debout.
    assert st["state"] == pc.UNVERIFIED
    assert st["expires_at"] is None
