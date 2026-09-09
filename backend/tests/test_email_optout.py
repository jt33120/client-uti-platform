"""
Désabonnement par type de notification.

Ce qui se paie cher ici, ce sont deux erreurs symétriques :

  • un lien qui désabonne QUELQU'UN D'AUTRE que son porteur — le jeton doit
    lier l'adresse ET la catégorie, pas seulement les transporter ;
  • un lien de désabonnement posé sur un email d'ACCÈS AU COMPTE (mot de passe
    oublié, invitation), qui offre au destinataire de se couper lui-même l'accès.

Le reste vérifie que le pied de page dit ce qu'il doit dire, dans les deux
versions du message (HTML et texte), et que rien de tout cela n'exige de base.
"""
import pytest

from services import email_optout as optout
from services import email_templates


@pytest.fixture(autouse=True)
def base_publique(monkeypatch):
    """Origine publique du BACKEND, d'où pend la route /emails/unsubscribe.

    Posée pour tout le module : sans elle, `unsubscribe_url` renvoie None (pas
    de lien plutôt qu'un lien mort) et les tests du pied de page vérifieraient
    une configuration absente au lieu du comportement. `PUBLIC_BASE_URL` est
    obligatoire dans le mode de stockage courant, donc c'est bien l'état de
    production que l'on reproduit ici.
    """
    from config import settings
    monkeypatch.setattr(settings, "public_base_url", "https://api.exemple.test")


# ── Le jeton lie une adresse à une catégorie ────────────────────────────────

def test_le_jeton_rend_exactement_ce_quil_a_signe():
    jeton = optout.sign("Marc.Dupont@Exemple.FR", "ao_new")
    assert optout.verify(jeton) == ("marc.dupont@exemple.fr", "ao_new")


def test_ladresse_est_normalisee_avant_signature():
    # Sinon un désabonnement ne prend pas parce que l'adresse a été saisie en
    # capitales quelque part dans la chaîne.
    assert optout.sign("  MARC@exemple.fr ", "ao_new") == optout.sign("marc@exemple.fr", "ao_new")


def test_un_jeton_trafique_est_refuse():
    jeton = optout.sign("marc@exemple.fr", "ao_new")
    with pytest.raises(ValueError):
        optout.verify(jeton[:-3] + "aaa")


def test_un_jeton_dune_autre_audience_est_refuse():
    # Un jeton de session ou d'URL de fichier ne doit jamais valoir
    # désabonnement, même signé avec une clé voisine.
    import jwt
    etranger = jwt.encode(
        {"e": "marc@exemple.fr", "c": "ao_new", "aud": "uti/file-url/v1"},
        optout._cle_de_signature(), algorithm=optout.ALG,
    )
    with pytest.raises(ValueError):
        optout.verify(etranger)


def test_une_categorie_inconnue_est_refusee():
    import jwt
    forge = jwt.encode(
        {"e": "marc@exemple.fr", "c": "tout", "aud": optout.AUDIENCE},
        optout._cle_de_signature(), algorithm=optout.ALG,
    )
    with pytest.raises(ValueError):
        optout.verify(forge)


# ── Ce qui est désabonnable, et ce qui ne doit surtout pas l'être ───────────

ACCES_AU_COMPTE = ["invite", "password_reset", "password_migration"]


@pytest.mark.parametrize("cle", ACCES_AU_COMPTE)
def test_aucun_lien_sur_les_emails_dacces_au_compte(cle):
    """Se désabonner d'un lien de réinitialisation, c'est se verrouiller dehors."""
    assert optout.category_for(cle) is None
    assert optout.unsubscribe_url("marc@exemple.fr", cle) is None


@pytest.mark.parametrize("cle", ACCES_AU_COMPTE)
def test_un_email_dacces_au_compte_part_toujours(cle):
    assert optout.is_blocked("marc@exemple.fr", cle) is False


def test_les_notifications_sont_desabonnables():
    for cle in ("ao_new", "ao_relance", "cv_retenu", "affaire_perdue", "annonce_pilote"):
        assert optout.category_for(cle), cle
        assert optout.label_for(cle), cle


def test_les_six_suivis_de_cv_partagent_une_categorie():
    # Se désabonner de « CV retenu » en restant abonné à « CV non retenu » n'a
    # pas de sens ; six cases pour un besoin unique font renoncer.
    cles = ["cv_retenu", "cv_non_retenu", "cv_envoye_client",
            "echange_commercial", "affaire_gagnee", "affaire_perdue"]
    assert {optout.category_for(c) for c in cles} == {"cv_suivi"}


def test_chaque_categorie_a_un_libelle():
    # Un pied de page qui annonce « abonné aux notifications «  » » est pire
    # qu'absent : il ne dit pas de quoi on se désabonne.
    assert set(optout.CATEGORIES.values()) <= set(optout.LABELS)


# ── Le pied de page, tel que le destinataire le lit ─────────────────────────

CONTEXTE = {
    "title": "Tech Lead", "client": "AGIRC", "reference": "AO-1",
    "location": "Paris", "deadline": "2026-07-15",
    "link": "https://exemple.test/aos/1", "partner_name": "Marc",
    "greeting": "Bonjour Marc,", "name": "Marc", "consultant": "Paul",
}


def test_la_notification_nomme_le_type_et_offre_le_lien():
    import html as _h
    _, html, texte = email_templates.build_email(
        "ao_new", CONTEXTE, recipient="marc@exemple.fr")
    assert "Vous recevez cet email car vous êtes abonné aux notifications" in html
    # Le libellé traverse l'échappement de la coquille (l'apostrophe de
    # « d'offres » devient &#x27;) : on compare donc à sa forme échappée, ce qui
    # vérifie du même coup qu'il est bien échappé.
    assert _h.escape("« Nouvel appel d'offres »") in html
    assert "Ne plus recevoir ce type de notification" in html
    assert "/emails/unsubscribe?token=" in html
    # Un client mail en texte seul ne doit pas être un client dont on ne peut
    # pas se désabonner.
    assert "Ne plus recevoir ce type de notification :" in texte
    assert "« Nouvel appel d'offres »" in texte
    assert "/emails/unsubscribe?token=" in texte


def test_le_lien_du_pied_de_page_desabonne_bien_ce_destinataire_la():
    import re
    _, html, _ = email_templates.build_email(
        "ao_relance", CONTEXTE, recipient="marc@exemple.fr")
    jeton = re.search(r"/emails/unsubscribe\?token=([\w.\-]+)", html).group(1)
    assert optout.verify(jeton) == ("marc@exemple.fr", "ao_relance")


def test_lemail_dacces_au_compte_garde_son_pied_de_page_et_na_pas_de_lien():
    _, html, texte = email_templates.build_email(
        "password_reset", {"link": "https://exemple.test/reset"},
        recipient="marc@exemple.fr")
    assert "votre mot de passe reste inchangé" in html
    assert "Ne plus recevoir" not in html
    assert "unsubscribe" not in html
    assert "Ne plus recevoir" not in texte


def test_sans_destinataire_aucun_lien_nest_signe():
    # L'aperçu sans destinataire ne doit pas fabriquer un lien qui désabonnerait
    # une adresse vide.
    _, html, _ = email_templates.build_email("ao_new", CONTEXTE)
    assert "unsubscribe" not in html


# ── L'en-tête que lisent Gmail et Outlook ──────────────────────────────────

@pytest.fixture
def expediteur(monkeypatch):
    """`build_message` a besoin d'une adresse d'expéditeur pour composer le From.

    Aucun défaut n'existe (et c'est voulu, cf. test_smtp_sans_defaut.py) : on la
    pose ici, faute de quoi on testerait la configuration SMTP au lieu de
    l'en-tête de désabonnement.
    """
    from config import settings
    monkeypatch.setattr(settings, "smtp_from", "no-reply@exemple.test")


def test_len_tete_list_unsubscribe_est_pose_quand_il_y_a_un_lien(expediteur):
    from services.email import build_message
    msg = build_message("marc@exemple.fr", "Sujet", "<p>x</p>",
                        unsubscribe_url="https://api.exemple.test/emails/unsubscribe?token=t")
    assert msg["List-Unsubscribe"] == "<https://api.exemple.test/emails/unsubscribe?token=t>"
    # Sans cet en-tête, Gmail n'affiche pas le bouton natif — et le geste de
    # repli du destinataire est « Spam », qui coûte au domaine entier.
    assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_aucun_en_tete_sur_un_email_transactionnel(expediteur):
    from services.email import build_message
    msg = build_message("marc@exemple.fr", "Mot de passe", "<p>x</p>")
    assert msg["List-Unsubscribe"] is None
    assert msg["List-Unsubscribe-Post"] is None
