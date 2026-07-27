"""IP publique affichée dans « dernière connexion ».

Le front passe par la réécriture ``/api/*`` de Vercel : ``X-Real-IP`` (posée par
notre nginx) vaut alors l'IP de sortie de Vercel, identique pour tout le monde.
La page Comptes affichait donc la même adresse (``15.237.x.x``) pour tous les
utilisateurs. L'IP réelle du navigateur est en tête de ``X-Forwarded-For``.
"""
from services.client_ip import public_client_ip


class _Client:
    def __init__(self, host):
        self.host = host


class _Req:
    """Requête minimale (duck-typing) : headers + pair TCP."""

    def __init__(self, headers=None, peer=None):
        self.headers = headers or {}
        self.client = _Client(peer) if peer else None


def test_prend_l_ip_utilisateur_pas_celle_du_proxy_vercel():
    # Chaîne réelle en prod : navigateur → Vercel → nginx (qui ajoute son pair).
    req = _Req(
        headers={
            "x-forwarded-for": "88.120.4.17, 15.237.118.237",
            "x-real-ip": "15.237.118.237",
        },
        peer="15.237.118.237",
    )
    assert public_client_ip(req) == "88.120.4.17"


def test_ignore_une_ip_privee_forgee_en_tete():
    # Un client qui pose lui-même un X-Forwarded-For bidon ne doit pas faire
    # afficher « 10.0.0.1 » comme IP de connexion.
    req = _Req(
        headers={"x-forwarded-for": "10.0.0.1, 192.168.1.5, 88.120.4.17"},
        peer="15.237.118.237",
    )
    assert public_client_ip(req) == "88.120.4.17"


def test_ignore_une_entree_malformee():
    req = _Req(headers={"x-forwarded-for": "pas-une-ip, 88.120.4.17"})
    assert public_client_ip(req) == "88.120.4.17"


def test_ipv6_publique_acceptee():
    req = _Req(headers={"x-forwarded-for": "2a01:e0a:1f2:3c40::1, 15.237.118.237"})
    assert public_client_ip(req) == "2a01:e0a:1f2:3c40::1"


def test_repli_sur_x_real_ip_sans_forwarded_for():
    req = _Req(headers={"x-real-ip": "88.120.4.17"}, peer="127.0.0.1")
    assert public_client_ip(req) == "88.120.4.17"


def test_dev_local_aucune_ip_publique():
    # En local tout est privé : on renvoie la valeur de confiance plutôt que None.
    req = _Req(headers={"x-real-ip": "127.0.0.1"}, peer="127.0.0.1")
    assert public_client_ip(req) == "127.0.0.1"


def test_sans_requete():
    assert public_client_ip(None) is None
