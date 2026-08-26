"""
`GET /matching/stats` : le KPI « AOs avec profil » ne compte que les AO vivants.

CE QUE CE TEST PROTÈGE

La table `matchings` ne porte aucune trace de l'état de l'AO qu'elle référence.
Compter ses lignes telles quelles fait donc vivre éternellement des AO clos :
constaté en production le 26 août 2026, avec 14 AO tous archivés, un KPI
« Appels d'offres » à 0 et, juste à côté, « AOs avec profil » à 4.

Le défaut est pernicieux parce qu'il ne ressemble pas à une panne — c'est un
nombre plausible, affiché sans erreur, à côté d'un autre nombre qui le
contredit. Rien dans l'exécution ne le signale ; seul un humain qui compare les
deux cases s'en aperçoit. D'où ce test.

Le prédicat attendu est celui de la vue « active » de `GET /aos`
(`is_draft = false` ET `archived = false`), et pas un autre : le KPI est
cliquable et mène à `/aos?matched=1`, qui intersecte `matched_ao_ids` avec
cette liste-là. Une définition divergente de « vivant » ferait promettre au
compteur des lignes que la page suivante n'afficherait pas.
"""
import asyncio

import pytest

from routers import matching

AO_VIVANT = "11111111-1111-1111-1111-111111111111"
AO_ARCHIVE = "22222222-2222-2222-2222-222222222222"
AO_BROUILLON = "33333333-3333-3333-3333-333333333333"

APPELS_OFFRES = [
    {"id": AO_VIVANT, "is_draft": False, "archived": False},
    {"id": AO_ARCHIVE, "is_draft": False, "archived": True},
    {"id": AO_BROUILLON, "is_draft": True, "archived": False},
]

MATCHINGS = [
    # Au-dessus du seuil, sur un AO vivant → doit compter.
    {"id": "m1", "ao_id": AO_VIVANT, "score_total": 72, "cost_usd": 0.01},
    # Au-dessus du seuil, mais l'AO est archivé → ne doit PAS compter.
    {"id": "m2", "ao_id": AO_ARCHIVE, "score_total": 88, "cost_usd": 0.01},
    # Au-dessus du seuil, mais l'AO est un brouillon → ne doit PAS compter.
    {"id": "m3", "ao_id": AO_BROUILLON, "score_total": 91, "cost_usd": 0.01},
    # Sous le seuil, sur un AO vivant → analysé, mais sans profil potentiel.
    {"id": "m4", "ao_id": AO_VIVANT, "score_total": 12, "cost_usd": 0.01},
]


class _Requete:
    """Bouchon minimal : `.select().eq().execute()` sur deux tables."""

    def __init__(self, table, lignes):
        self._table = table
        self._lignes = lignes
        self._filtres = {}

    def select(self, *_a, **_k):
        return self

    def eq(self, colonne, valeur):
        self._filtres[colonne] = valeur
        return self

    def execute(self):
        lignes = [
            r for r in self._lignes
            if all(r.get(c) == v for c, v in self._filtres.items())
        ]
        return type("Resultat", (), {"data": lignes})()


class _Supabase:
    def __init__(self, tables):
        self._tables = tables
        self.tables_lues = []

    def table(self, nom):
        self.tables_lues.append(nom)
        return _Requete(nom, self._tables.get(nom, []))


@pytest.fixture
def faux_supabase(monkeypatch):
    faux = _Supabase({"appels_offres": APPELS_OFFRES, "matchings": MATCHINGS})
    monkeypatch.setattr(matching, "supabase", faux)
    return faux


def _stats():
    return asyncio.run(matching.get_matching_stats(user={"sub": "test", "role": "admin"}))


def test_les_ao_archives_ne_comptent_pas(faux_supabase):
    """Le cas exact de la production : un AO archivé bien noté ne compte plus."""
    stats = _stats()
    assert stats["aos_matched"] == 1
    assert stats["matched_ao_ids"] == [AO_VIVANT]
    assert AO_ARCHIVE not in stats["matched_ao_ids"]


def test_les_brouillons_ne_comptent_pas(faux_supabase):
    """Un AO jamais publié n'a pas à peupler un indicateur métier."""
    assert AO_BROUILLON not in _stats()["matched_ao_ids"]


def test_aos_analyses_exclut_aussi_les_morts(faux_supabase):
    """`aos_analyzed` suit la même règle : sinon les deux chiffres divergent."""
    assert _stats()["aos_analyzed"] == 1


def test_le_seuil_reste_applique_aux_ao_vivants(faux_supabase):
    """Filtrer sur l'état de l'AO ne doit pas désarmer le seuil de score."""
    stats = _stats()
    assert stats["potential_threshold"] == 50
    # AO_VIVANT porte un 72 ET un 12 : il compte une fois, pas deux.
    assert stats["matched_ao_ids"].count(AO_VIVANT) == 1


def test_tous_archives_donne_zero(monkeypatch):
    """Le cas signalé : plus aucun AO vivant → le KPI doit tomber à 0, pas à 4."""
    faux = _Supabase({
        "appels_offres": [{"id": AO_ARCHIVE, "is_draft": False, "archived": True}],
        "matchings": [{"id": "m", "ao_id": AO_ARCHIVE, "score_total": 99, "cost_usd": 0.0}],
    })
    monkeypatch.setattr(matching, "supabase", faux)
    stats = _stats()
    assert stats["aos_matched"] == 0
    assert stats["matched_ao_ids"] == []


def test_lecture_des_ao_en_echec_ne_ressuscite_pas_les_archives(monkeypatch):
    """Fail-closed : si `appels_offres` est illisible, on affiche 0, jamais tout.

    Un repli sur le comptage non filtré rendrait le défaut invisible ET
    intermittent — le pire des deux mondes.
    """
    class _Cassee(_Supabase):
        def table(self, nom):
            if nom == "appels_offres":
                raise RuntimeError("PostgREST injoignable")
            return super().table(nom)

    monkeypatch.setattr(matching, "supabase", _Cassee({"matchings": MATCHINGS}))
    stats = _stats()
    assert stats["aos_matched"] == 0
    assert stats["matched_ao_ids"] == []
