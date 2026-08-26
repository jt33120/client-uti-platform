"""
Le bandeau de migration ne doit rien AFFIRMER sur un compte qu'il ne connaît pas.

CE QUE CE TEST PROTÈGE

`LoginPage.jsx` affiche ce bandeau après TOUT 401, délibérément : ne l'afficher
qu'aux comptes connus révélerait qui a un compte. La condition est bonne et ne
doit pas changer.

C'est la rédaction qui posait problème. Elle disait « Votre compte et vos
données sont intacts » — une affirmation de fait, adressée à quelqu'un qui n'a
peut-être aucun compte, et fausse aussi pour qui a déjà repris la main et vient
simplement de mal taper son mot de passe.

Le 26 août 2026, le fondateur a saisi une adresse sans compte, lu cette phrase,
et en a conclu qu'un compte avait été SUPPRIMÉ. Un message écrit pour rassurer a
fabriqué la croyance d'une perte de données, puis coûté une enquête.

À NE PAS CONFONDRE AVEC L'E-MAIL DE MIGRATION

`email_templates` « password_migration » affirme la même chose, et c'est
LÉGITIME : il n'est envoyé qu'après avoir trouvé un compte réel à migrer
(routers/auth.py, branche `migre`). Une affirmation n'est fausse que si son
destinataire peut ne pas être concerné. Ce test ne vise donc que la page de
connexion.
"""
import pathlib
import re

LOGIN = (
    pathlib.Path(__file__).resolve().parents[2]
    / "frontend" / "src" / "pages" / "LoginPage.jsx"
)


def source() -> str:
    return LOGIN.read_text(encoding="utf-8")


def test_le_bandeau_ne_dit_plus_votre_compte_est_intact():
    src = source()
    assert "Votre compte et vos données sont intacts" not in src, (
        "Le bandeau affirme de nouveau un fait sur le compte du lecteur, alors "
        "qu'il s'affiche aussi à qui n'en a pas."
    )


def test_le_bandeau_conditionne_son_affirmation():
    assert "Si vous aviez un compte" in source(), (
        "La formulation conditionnelle a disparu : le bandeau doit énoncer une "
        "hypothèse, pas un état de fait."
    )


def test_le_bandeau_s_affiche_toujours_sur_tout_401():
    """La confidentialité ne doit pas être sacrifiée en corrigeant la rédaction.

    Si quelqu'un « corrigeait » le problème en n'affichant le bandeau qu'aux
    comptes connus, la page deviendrait un oracle d'existence de comptes.
    """
    src = source()
    assert re.search(r"status\s*===\s*401\)\s*setHeritage\(true\)", src), (
        "La condition d'affichage a changé. Elle doit rester « tout 401 », "
        "sans quoi le bandeau révèle qui possède un compte."
    )


def test_le_texte_reste_identique_quelle_que_soit_l_adresse():
    """Aucune interpolation de l'adresse saisie dans le bandeau.

    Réinjecter l'e-mail rendrait le message spécifique, donc potentiellement
    révélateur, et rouvrirait exactement la porte que la condition ferme.
    """
    src = source()
    debut = src.find("{heritage && (")
    assert debut != -1, "Le bandeau a disparu de la page."
    bandeau = src[debut:debut + 3000]
    assert "form.email" not in bandeau.split("to={`/forgot-password")[0], (
        "L'adresse saisie est interpolée dans le texte du bandeau."
    )
