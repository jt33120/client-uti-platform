"""
Aucun serveur SMTP par défaut, et une file qui annonce sa mise en pause.

CE QUE CES TESTS PROTÈGENT

`config.py` portait `smtp_host = "mail.infomaniak.com"`. Le `.env` de production
ne définissait NI `SMTP_HOST` ni `SMTP_PORT` : la plateforme envoyait donc son
courrier depuis un hébergeur qui n'apparaissait dans aucun fichier de
configuration, et dont plus personne dans l'équipe ne détenait le compte. Le
défaut n'a rendu service à personne — il a caché une configuration manquante
derrière un comportement plausible pendant des mois.

Remettre un défaut, fût-ce celui du fournisseur du jour, rejouerait exactement
ça : le jour où quelqu'un oubliera `SMTP_HOST`, la production repartirait
silencieusement vers un tiers arbitraire au lieu de signaler le trou.

Le second test couvre l'autre moitié du problème. Sans configuration SMTP,
`process_outbox` renvoyait `status="disabled"` — et la boucle du planificateur
n'imprimait QUE lorsqu'un envoi avait eu lieu. Une file en pause tournait donc à
vide toutes les 20 secondes sans laisser la moindre trace : « aucun e-mail ne
part » ne se découvrait qu'en le constatant chez un utilisateur, des jours plus
tard. Un état qui empêche le service de fonctionner doit s'entendre.
"""
import importlib

import pytest


def test_aucun_serveur_smtp_par_defaut(monkeypatch):
    """`SMTP_HOST` absent doit rester absent — jamais un fournisseur en dur."""
    monkeypatch.delenv("SMTP_HOST", raising=False)
    import config
    importlib.reload(config)
    assert config.settings.smtp_host is None, (
        "Un défaut a été réintroduit pour SMTP_HOST. Voir l'en-tête de ce "
        "fichier : c'est ce défaut qui a fait tourner la production chez un "
        "hébergeur absent de toute configuration."
    )


def test_smtp_host_absent_est_signale_nommement():
    """Le motif doit NOMMER la variable manquante, pas dire « e-mail indisponible »."""
    from services import email
    from config import settings

    original = settings.smtp_host
    try:
        settings.smtp_host = None
        assert email.config_error() == "SMTP_HOST non configuré"
    finally:
        settings.smtp_host = original


def test_la_file_en_pause_le_dit_une_fois_puis_se_tait():
    """Reproduit la logique d'annonce de `run_outbox_worker`.

    Deux exigences opposées : une pause doit se voir (sinon elle passe pour un
    fonctionnement normal), et ne doit pas se répéter toutes les 20 secondes
    (sinon elle noie le journal et redevient invisible).
    """
    lignes = []
    pause_annoncee = None

    def tick(res):
        nonlocal pause_annoncee
        motif = res.get("reason") if res.get("status") == "disabled" else None
        if motif != pause_annoncee:
            if motif:
                lignes.append(f"EN PAUSE : {motif}")
            elif pause_annoncee:
                lignes.append("REPRISE")
            pause_annoncee = motif

    en_pause = {"status": "disabled", "reason": "SMTP_HOST non configuré"}
    ok = {"status": "ok", "sent": 0, "failed": 0}

    tick(en_pause); tick(en_pause); tick(en_pause)
    assert lignes == ["EN PAUSE : SMTP_HOST non configuré"], "la pause s'est répétée"

    tick(ok)
    assert lignes[-1] == "REPRISE", "le retour à la normale doit se dire aussi"

    tick(en_pause)
    assert lignes[-1] == "EN PAUSE : SMTP_HOST non configuré", (
        "une pause qui revient après une reprise doit être ré-annoncée"
    )


def test_la_boucle_annonce_bien_la_pause():
    """Garde-fou : le code de `run_outbox_worker` porte réellement l'annonce.

    Le test précédent vérifie une logique reproduite ; celui-ci vérifie qu'elle
    n'a pas été retirée du fichier qu'elle est censée décrire.
    """
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "services" / "scheduler.py"
    contenu = src.read_text(encoding="utf-8")
    assert "EN PAUSE" in contenu
    assert "pause_annoncee" in contenu
