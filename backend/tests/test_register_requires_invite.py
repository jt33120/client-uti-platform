"""
Garde-fou : la création de compte reste sur invitation, côté SERVEUR.

Le front l'imposait déjà (RegisterPage n'affiche aucun formulaire sans jeton),
mais le backend ne l'exigeait pas. Sans `invite_token`, le bloc de validation de
l'invitation était sauté et `body.role` — fourni par l'appelant — était retenu
tel quel ; le seul contrôle restant vérifiait que le rôle faisait partie des
rôles connus, ce qui inclut « admin ». Un POST direct sur la route publique
créait donc un administrateur.

Ce test lit la SOURCE plutôt que d'importer le module, pour la même raison que
test_schema_versioned.py : un test qui se met en `skip` quand une dépendance
manque ne garde rien, et c'est précisément dans un environnement dépouillé (CI
minimale, image de production) qu'on aimerait qu'il parle.
"""
import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parents[1] / "routers" / "auth.py"


def _register_fn() -> ast.AsyncFunctionDef:
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "register":
            return node
    raise AssertionError("La fonction register() est introuvable dans routers/auth.py")


def test_register_refuses_without_invite_token():
    """Un `raise HTTPException(403)` doit garder l'absence de jeton d'invitation."""
    fn = _register_fn()

    gardes = [
        node for node in ast.walk(fn)
        if isinstance(node, ast.If)
        # `if not body.invite_token:` — négation d'un accès à l'attribut
        and isinstance(node.test, ast.UnaryOp)
        and isinstance(node.test.op, ast.Not)
        and isinstance(node.test.operand, ast.Attribute)
        and node.test.operand.attr == "invite_token"
    ]
    assert gardes, (
        "register() ne refuse plus l'absence d'invitation. Sans ce garde-fou, "
        "le rôle demandé par l'appelant est conservé tel quel et n'importe qui "
        "peut se créer un compte admin."
    )

    leve_403 = any(
        isinstance(n, ast.Raise)
        and isinstance(n.exc, ast.Call)
        and any(
            kw.arg == "status_code" and getattr(kw.value, "value", None) == 403
            for kw in n.exc.keywords
        )
        for garde in gardes
        for n in ast.walk(garde)
    )
    assert leve_403, "Le garde-fou existe mais ne lève pas de 403."


def test_register_is_rate_limited():
    """Route publique et non authentifiée : elle doit être limitée en débit.

    Sans cela, même fermée aux rôles arbitraires, elle reste un point d'appui
    pour l'énumération d'adresses et la création de comptes en masse via des
    invitations devinées.
    """
    fn = _register_fn()
    appels = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_throttle"
    ]
    assert appels, "register() n'appelle plus _throttle() : la route est sans limite de débit."


def test_role_is_forced_from_the_invitation():
    """Le rôle doit venir de l'invitation, jamais du corps de la requête.

    C'est la seconde moitié de la protection : exiger un jeton ne suffirait pas
    si le rôle demandé par l'appelant l'emportait sur celui de l'invitation.
    """
    fn = _register_fn()
    ecrase = any(
        isinstance(n, ast.Assign)
        and any(
            isinstance(t, ast.Attribute) and t.attr == "role"
            and getattr(t.value, "id", None) == "body"
            for t in n.targets
        )
        and isinstance(n.value, ast.Subscript)
        for n in ast.walk(fn)
    )
    assert ecrase, (
        "register() n'écrase plus body.role par invitation[\"role\"] : le rôle "
        "envoyé par l'appelant ferait autorité."
    )
