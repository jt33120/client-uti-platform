"""
Conformité partenaire — obligation de vigilance (art. L.8222-1 code du travail).

Ce module ne porte que la RÈGLE : quelles pièces, quelle validité, quel état.
Le stockage et les écrans vivent ailleurs.

⚠️ Les paramètres suivent la lecture consignée dans
`compliance/QUESTIONS-CONSEIL-JURIDIQUE.md` (point D1), EN ATTENTE de
confirmation par le conseil. Trois points s'écartent de ce qu'écrivent la plupart
des éditeurs de solutions de conformité, et méritent d'être signalés :

  1. le seuil de 5 000 € HT s'apprécie PAR OPÉRATION (art. R.8222-1), et non par
     cumul annuel avec un même prestataire ;
  2. les pièces de l'art. L.8222-1 sont au nombre de DEUX (attestation de
     vigilance + justificatif d'immatriculation), la liste nominative des
     salariés étrangers relevant d'un régime distinct (art. L.8254-1) ;
  3. détenir l'attestation ne suffit pas : le donneur d'ordre doit s'assurer de
     son AUTHENTICITÉ auprès de l'URSSAF. Un PDF téléversé et jamais vérifié
     laisse la solidarité financière entière.

Volontairement en mode ALERTE et non blocage : l'obligation se rattache au
contrat de prestation, pas à la présentation d'une candidature. Bloquer l'envoi
d'un CV serait juridiquement inutile et commercialement absurde.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Optional

# Fenêtre d'alerte avant échéance : laisse le temps de redemander la pièce au
# partenaire sans être déjà en défaut.
EXPIRY_WARNING_DAYS = 30

VIGILANCE = "vigilance"
IMMATRICULATION = "immatriculation"
SALARIES_ETRANGERS = "salaries_etrangers"

DOC_TYPES = {
    VIGILANCE: {
        "label": "Attestation de vigilance URSSAF",
        "legal": "art. L.8222-1 c. trav. · L.243-15 CSS",
        # D.8222-5 : vérification tous les six mois jusqu'à la fin d'exécution.
        "validity_months": 6,
        "requires_authenticity_check": True,
        "required": True,
    },
    IMMATRICULATION: {
        "label": "Justificatif d'immatriculation (Kbis, RNE)",
        "legal": "art. D.8222-5 c. trav. · R.123-6 c. com.",
        # Aucune durée légale : c'est un USAGE (3 mois) qu'on retient comme
        # seuil d'alerte, pas une péremption réglementaire. D'où `advisory`.
        "validity_months": 3,
        "advisory": True,
        "requires_authenticity_check": False,
        "required": True,
    },
    SALARIES_ETRANGERS: {
        "label": "Liste nominative des salariés étrangers",
        "legal": "art. L.8254-1 et D.8254-2 c. trav.",
        # Exigible À LA CONCLUSION seulement, et uniquement si le partenaire
        # emploie des salariés soumis à autorisation de travail : pas de
        # périodicité, et pas « requis » par défaut pour tout le monde.
        "validity_months": None,
        "requires_authenticity_check": False,
        "required": False,
    },
}

# États, du plus au moins urgent.
MISSING = "missing"          # aucune pièce déposée
EXPIRED = "expired"          # hors délai
UNVERIFIED = "unverified"    # déposée et dans les délais, mais authenticité non vérifiée
EXPIRING = "expiring"        # valable, mais échéance sous 30 jours
VALID = "valid"

_ORDER = {MISSING: 0, EXPIRED: 1, UNVERIFIED: 2, EXPIRING: 3, VALID: 4}


def _as_date(v) -> Optional[date]:
    if not v:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        return None


def expires_on(doc_type: str, issued_at) -> Optional[date]:
    """Échéance d'une pièce, ou None si le type n'a pas de périodicité."""
    spec = DOC_TYPES.get(doc_type) or {}
    months = spec.get("validity_months")
    issued = _as_date(issued_at)
    if not months or not issued:
        return None
    # 30 jours par mois : même convention que services/data_retention.py, et
    # légèrement conservatrice (l'échéance tombe un peu tôt), ce qui est le bon
    # sens de l'erreur pour une obligation de vigilance.
    return issued + timedelta(days=int(months) * 30)


def doc_status(doc: dict, today: Optional[date] = None) -> dict:
    """État d'une pièce déposée."""
    today = today or datetime.now(timezone.utc).date()
    doc_type = doc.get("doc_type")
    spec = DOC_TYPES.get(doc_type) or {}
    due = expires_on(doc_type, doc.get("issued_at"))
    checked = bool(doc.get("authenticity_checked_at"))

    if due and due < today:
        state = EXPIRED
    elif spec.get("requires_authenticity_check") and not checked:
        # Une attestation dans les délais mais non vérifiée ne purge pas
        # l'obligation : on ne la compte donc pas comme valide.
        state = UNVERIFIED
    elif due and (due - today).days <= EXPIRY_WARNING_DAYS:
        state = EXPIRING
    else:
        state = VALID

    return {
        "state": state,
        "expires_at": due.isoformat() if due else None,
        "days_left": (due - today).days if due else None,
        "authenticity_checked": checked,
        "advisory": bool(spec.get("advisory")),
    }


def partner_status(docs: list[dict], today: Optional[date] = None) -> dict:
    """Synthèse pour un partenaire, à partir de TOUTES ses pièces.

    La pièce courante d'un type est la plus récemment émise ; l'historique reste
    en base pour démontrer qu'on demandait bien les pièces à l'époque.
    """
    today = today or datetime.now(timezone.utc).date()

    current: dict[str, dict] = {}
    for d in docs or []:
        t = d.get("doc_type")
        if t not in DOC_TYPES:
            continue
        prev = current.get(t)
        if not prev or (_as_date(d.get("issued_at")) or date.min) >= (_as_date(prev.get("issued_at")) or date.min):
            current[t] = d

    by_type = {}
    for t, spec in DOC_TYPES.items():
        doc = current.get(t)
        if not doc:
            by_type[t] = {
                "state": MISSING, "label": spec["label"], "legal": spec["legal"],
                "required": spec["required"], "advisory": bool(spec.get("advisory")),
                "doc": None,
            }
            continue
        st = doc_status(doc, today)
        by_type[t] = {**st, "label": spec["label"], "legal": spec["legal"],
                      "required": spec["required"], "doc": doc}

    # L'état global ne tient compte que des pièces REQUISES : une liste de
    # salariés étrangers absente chez un partenaire qui n'en emploie pas ne doit
    # pas afficher le partenaire en défaut.
    required_states = [v["state"] for t, v in by_type.items() if DOC_TYPES[t]["required"]]
    overall = min(required_states, key=lambda s: _ORDER.get(s, 9)) if required_states else VALID

    return {
        "overall": overall,
        "ok": overall in (VALID, EXPIRING),
        "by_type": by_type,
        "warning_days": EXPIRY_WARNING_DAYS,
    }
