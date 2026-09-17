"""
Les champs requis pour publier un AO : une seule liste, deux fichiers.

POURQUOI CE FICHIER EXISTE

Publier un AO est gardé DEUX FOIS : le formulaire refuse d'envoyer
(NewAOPage.jsx), et l'API refuse de publier (routers/aos.py:
_PUBLISH_REQUIRED_FIELDS). C'est une bonne chose — le formulaire donne un
message immédiat et lisible, l'API garde la règle même si quelqu'un appelle
l'API directement.

Mais deux gardes, ce sont deux listes, et deux listes finissent par diverger.
Et la divergence n'est pas symétrique :

  * le FRONT plus strict que l'API : un champ réclamé par le formulaire alors
    que l'API s'en passe. L'utilisateur remplit pour rien, et on ne le saura
    jamais — personne ne signale un champ qu'on lui a demandé de remplir.
  * l'API plus stricte que le FRONT : le formulaire accepte, l'appel part, et
    l'API répond 422. L'utilisateur voit son AO refusé après coup, sans
    comprendre, sur un champ que rien ne signalait.

Le 17 septembre 2026, le client a demandé que Budget et Localisation ne soient
plus obligatoires : ces informations manquent réellement sur certains AO, et les
exiger obligeait à inventer une valeur ou à laisser l'AO en brouillon — donc
invisible des partenaires et non matché. Les deux gardes ont été modifiées ;
ce test est ce qui garantit qu'elles l'ont été ENSEMBLE.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
RACINE = BACKEND.parent
ROUTEUR = BACKEND / "routers" / "aos.py"
FORMULAIRE = RACINE / "frontend" / "src" / "pages" / "NewAOPage.jsx"


def champs_requis_api() -> dict[str, str]:
    """_PUBLISH_REQUIRED_FIELDS, lu par analyse syntaxique.

    On lit le fichier plutôt que de l'importer : `routers/aos.py` tire FastAPI,
    la configuration et un client Supabase, dont l'absence mettrait ce test en
    « erreur de collecte » — c'est-à-dire silencieux, précisément là où on
    voudrait qu'il parle.
    """
    arbre = ast.parse(ROUTEUR.read_text())
    for noeud in ast.walk(arbre):
        if (isinstance(noeud, ast.Assign)
                and any(getattr(c, "id", None) == "_PUBLISH_REQUIRED_FIELDS"
                        for c in noeud.targets)):
            return ast.literal_eval(noeud.value)
    raise AssertionError("_PUBLISH_REQUIRED_FIELDS introuvable dans routers/aos.py")


def champs_requis_formulaire() -> list[str]:
    """Les libellés exigés par le formulaire pour une PUBLICATION.

    Uniquement ceux du bloc `if (!asDraft) { … }` : les champs exigés en toutes
    circonstances (Client, Titre, Description, Compétences) sont hors sujet —
    ils valent aussi pour un brouillon, et l'API les impose par son schéma.
    """
    texte = FORMULAIRE.read_text()
    m = re.search(r"if \(!asDraft\) \{(.*?)\n    \}", texte, re.S)
    assert m, "le bloc de validation « if (!asDraft) » n'a plus la forme attendue"
    return re.findall(r"missing\.push\(['\"](.+?)['\"]\)", m.group(1))


def test_le_formulaire_et_lapi_exigent_exactement_la_meme_chose():
    api = set(champs_requis_api().values())
    front = set(champs_requis_formulaire())
    assert front == api, (
        "les deux gardes de publication ont divergé.\n"
        f"  exigé par le formulaire seul : {sorted(front - api) or '—'}\n"
        f"  exigé par l'API seule        : {sorted(api - front) or '—'}\n"
        "Le premier cas fait remplir un champ pour rien ; le second fait "
        "refuser l'AO après coup, sur un champ que rien ne signalait."
    )


def test_budget_et_localisation_ne_sont_plus_exiges():
    """La demande client du 17/09/2026, tenue des deux côtés."""
    api = champs_requis_api()
    front = champs_requis_formulaire()
    assert "budget_max" not in api and "location" not in api, (
        f"l'API exige encore : {sorted(api)}"
    )
    assert "Budget max" not in front and "Localisation" not in front, (
        f"le formulaire exige encore : {sorted(front)}"
    )


def test_le_formulaire_ne_marque_plus_ces_champs_dune_etoile():
    """Un champ étoilé qui n'est plus obligatoire est un mensonge d'interface :
    l'utilisateur le remplit par obéissance, et perd confiance dans les autres
    étoiles le jour où il découvre que celle-là ne voulait rien dire."""
    texte = FORMULAIRE.read_text()
    for libelle in ("Budget max (€/jour)", "Localisation"):
        assert f"{libelle} *" not in texte, (
            f"« {libelle} » porte encore l'astérisque des champs obligatoires"
        )


def test_les_champs_encore_exiges_le_restent():
    """Garde-fou dans l'autre sens : assouplir deux champs ne doit pas avoir
    vidé la règle. Un AO publié sans référence ni date limite serait invisible
    dans les recherches des partenaires."""
    api = champs_requis_api()
    for cle in ("reference", "ao_type", "deadline", "duration"):
        assert cle in api, f"{cle} n'est plus exigé pour publier — était-ce voulu ?"


def test_un_ao_sans_budget_ni_localisation_peut_etre_publie():
    """Le comportement réel, joué sur la fonction de l'API.

    On rejoue `_missing_publish_fields` — extraite du routeur, pas recopiée —
    contre un AO tel que le client en dépose : complet, sauf le budget et le
    lieu qu'il n'a pas.
    """
    requis = champs_requis_api()

    def manquants(record: dict) -> list[str]:
        return [label for cle, label in requis.items() if not record.get(cle)]

    ao_du_client = {
        "reference": "AO-2026-114",
        "ao_type": "IT / Dev",
        "deadline": "2026-10-15",
        "duration": "6 mois",
        "budget_max": None,
        "location": None,
    }
    assert manquants(ao_du_client) == [], (
        f"publication refusée sur : {manquants(ao_du_client)}"
    )

    # …et la règle mord toujours sur ce qui reste exigé.
    sans_duree = dict(ao_du_client, duration=None)
    assert manquants(sans_duree) == ["Durée"]
