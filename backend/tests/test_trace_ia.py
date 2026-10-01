"""
Chaque appel IA doit être rattaché à la requête qui l'a déclenché — et à son agent.

POURQUOI CE FICHIER EXISTE

Jusqu'au 1er octobre 2026, la mesure de chaque appel IA (mip_rum_ai.py) partait
avec un identifiant de trace tiré au hasard. Elle était orpheline : impossible de
la rattacher à la requête HTTP qui l'avait déclenchée, ni au clic du navigateur
derrière cette requête. Le middleware connaissait pourtant ce contexte — mais il
ne le calculait qu'APRÈS la réponse, trop tard pour qu'un appel IA s'y accroche.

Et côté xSOM AI Guard, où toute la surveillance IA doit désormais être rattachée,
toutes les fonctions IA d'UTI partaient sous un même jeton — donc comme UN seul
agent, alors qu'un agent xSOM EST un jeton de passerelle.

Ces tests exécutent le vrai middleware et le vrai émetteur, contre des bouchons
réseau. Ce qu'ils défendent :

  * le contexte de trace est posé AVANT l'application, et retiré après ;
  * un appel IA fait pendant une requête porte SA trace, a SON span pour parent,
    et hérite de SA session ;
  * hors requête, une trace neuve est ouverte (le planificateur n'hérite de rien) ;
  * chaque appel part sous le jeton de son agent, avec un défaut qui ne perd rien.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import mip_rum_middleware  # noqa: E402  (bibliothèque standard seulement)
import request_trace  # noqa: E402

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
SPAN_NAVIGATEUR = "00f067aa0ba902b7"
TRACEPARENT = f"00-{TRACE}-{SPAN_NAVIGATEUR}-01"
TRACESTATE = "mip=s:session_42"


# ═══════════════════════════════════════════════════════════════════════════
#  Outillage
# ═══════════════════════════════════════════════════════════════════════════
def charger_emetteur(monkeypatch, **reglages):
    """Le vrai mip_rum_ai.py, chargé avec une configuration de pacotille.

    L'émetteur lit ses réglages à l'import (``from config import settings``), et
    le vrai config.py exige pydantic-settings et toute la configuration de
    production. On lui substitue un module ``config`` le temps du test, et on
    charge l'émetteur sous un nom privé : le module réel n'est jamais touché, et
    rien ne fuit vers les autres tests.

    ``_emit`` est remplacé par une capture : on observe exactement le span et le
    jeton qui partiraient, sans thread ni réseau.
    """
    defauts = dict(
        mip_rum_endpoint=None, mip_rum_app_id="gip-plateforme", mip_rum_api_key=None,
        xsom_ai_url="https://xsom.invalide/v1", xsom_gateway_token="xsg_defaut",
        xsom_agent_tokens=None,
    )
    defauts.update(reglages)
    faux_config = types.ModuleType("config")
    faux_config.settings = types.SimpleNamespace(**defauts)
    monkeypatch.setitem(sys.modules, "config", faux_config)

    spec = importlib.util.spec_from_file_location("mip_rum_ai_sous_test", BACKEND / "mip_rum_ai.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    captures = []
    monkeypatch.setattr(module, "_emit", lambda span, xsom_token=None: captures.append((span, xsom_token)))
    return module, captures


def attribut(span: dict, cle: str):
    for a in span.get("attributes") or []:
        if a.get("key") == cle:
            v = a.get("value") or {}
            return next(iter(v.values()), None)
    return None


def appel_ia(emetteur, route="matching/score", **kw):
    with emetteur.record_ai_call(provider="openrouter", model="mistral-small", route=route, **kw):
        pass


def requete(path="/aos/42/matching", traceparent=TRACEPARENT, tracestate=TRACESTATE):
    entetes = []
    if traceparent:
        entetes.append((b"traceparent", traceparent.encode()))
    if tracestate:
        entetes.append((b"tracestate", tracestate.encode()))
    return {"type": "http", "method": "POST", "path": path, "headers": entetes}


async def jouer(middleware, scope, pendant=None):
    """Fait passer une requête dans le middleware ; ``pendant`` tourne DANS l'app."""
    vu = {}

    async def application(scope, receive, send):
        vu["ctx"] = request_trace.current()
        if pendant:
            pendant()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message):
        return None

    middleware.app = application
    await middleware(scope, receive, send)
    vu["apres"] = request_trace.current()
    if middleware._task:
        middleware._task.cancel()
    return vu


def middleware_actif(monkeypatch):
    """Le vrai middleware, envoi vers MIP actif mais réseau coupé."""
    monkeypatch.setattr(mip_rum_middleware, "_post", lambda *a, **k: None)
    return mip_rum_middleware.MIPRumMiddleware(
        app=None, endpoint="https://mip.invalide/v1/traces", app_id="gip-plateforme",
        flush_s=3600, batch_size=10_000,
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Le contexte de trace
# ═══════════════════════════════════════════════════════════════════════════
def test_le_contexte_reprend_la_trace_du_navigateur():
    ctx = request_trace.from_headers(TRACEPARENT, TRACESTATE)
    assert ctx.trace_id == TRACE
    assert ctx.parent_span_id == SPAN_NAVIGATEUR
    assert ctx.session_id == "session_42"
    assert len(ctx.span_id) == 16 and ctx.span_id != SPAN_NAVIGATEUR


@pytest.mark.parametrize("traceparent", [None, "", "n'importe quoi", "01-" + TRACE + "-" + SPAN_NAVIGATEUR + "-01"])
def test_sans_traceparent_valide_une_trace_neuve_est_ouverte(traceparent):
    """Une requête n'est jamais refusée pour sa trace : appel hors navigateur,
    en-tête malformé ou version inconnue → trace neuve, sans parent."""
    ctx = request_trace.from_headers(traceparent, None)
    assert len(ctx.trace_id) == 32 and ctx.trace_id != TRACE
    assert ctx.parent_span_id is None
    assert ctx.session_id is None


# ═══════════════════════════════════════════════════════════════════════════
#  Le middleware pose le contexte AVANT l'application
# ═══════════════════════════════════════════════════════════════════════════
def test_lapplication_voit_le_contexte_pendant_la_requete(monkeypatch):
    """Le défaut d'origine : le contexte n'était calculé qu'APRÈS la réponse,
    dans _record. Aucun appel IA fait pendant la requête ne pouvait s'y accrocher."""
    vu = asyncio.run(jouer(middleware_actif(monkeypatch), requete()))
    assert vu["ctx"] is not None, "aucun contexte de trace pendant la requête"
    assert vu["ctx"].trace_id == TRACE
    assert vu["ctx"].session_id == "session_42"


def test_le_contexte_ne_fuit_pas_hors_de_la_requete(monkeypatch):
    """Sans retrait, la requête suivante traitée par le même fil hériterait de la
    trace de la précédente — et ses appels IA seraient rattachés au mauvais clic."""
    vu = asyncio.run(jouer(middleware_actif(monkeypatch), requete()))
    assert vu["apres"] is None, "le contexte de trace a survécu à la requête"


def test_le_span_http_et_le_contexte_partagent_leurs_identifiants(monkeypatch):
    """Le span http.server envoyé à MIP doit porter EXACTEMENT les identifiants
    que les appels IA ont vus : c'est ce qui en fait leur parent."""
    mw = middleware_actif(monkeypatch)
    vu = asyncio.run(jouer(mw, requete()))
    span_http = mw._buf[-1]
    assert span_http["traceId"] == vu["ctx"].trace_id == TRACE
    assert span_http["spanId"] == vu["ctx"].span_id
    assert span_http["parentSpanId"] == SPAN_NAVIGATEUR


def test_le_contexte_est_pose_meme_si_lenvoi_vers_mip_est_inactif(monkeypatch):
    """La surveillance IA part vers xSOM, que MIP soit configuré ou non : le
    rattachement des appels IA à la requête ne doit pas dépendre de MIP."""
    monkeypatch.delenv("MIP_RUM_ENDPOINT", raising=False)
    mw = mip_rum_middleware.MIPRumMiddleware(app=None, endpoint=None, app_id=None)
    assert not mw.enabled
    vu = asyncio.run(jouer(mw, requete()))
    assert vu["ctx"] is not None and vu["ctx"].trace_id == TRACE
    assert vu["apres"] is None


# ═══════════════════════════════════════════════════════════════════════════
#  L'appel IA reprend la trace de sa requête
# ═══════════════════════════════════════════════════════════════════════════
def test_un_appel_ia_pendant_une_requete_porte_sa_trace(monkeypatch):
    emetteur, captures = charger_emetteur(monkeypatch)
    vu = asyncio.run(jouer(middleware_actif(monkeypatch), requete(),
                           pendant=lambda: appel_ia(emetteur)))
    span, _ = captures[-1]
    assert span["traceId"] == TRACE, "la mesure IA est encore orpheline"
    assert span["parentSpanId"] == vu["ctx"].span_id, (
        "la mesure IA n'a pas le span http.server de sa requête pour parent"
    )
    assert attribut(span, "mip.session_id") == "session_42", (
        "la mesure IA n'hérite pas de la session du navigateur"
    )


def test_une_session_explicite_garde_la_priorite(monkeypatch):
    """routers/assistant.py passe déjà sa session : le contexte ne doit pas
    l'écraser, seulement combler son absence."""
    emetteur, captures = charger_emetteur(monkeypatch)
    asyncio.run(jouer(middleware_actif(monkeypatch), requete(),
                      pendant=lambda: appel_ia(emetteur, session_id="session_explicite")))
    assert attribut(captures[-1][0], "mip.session_id") == "session_explicite"


def test_hors_requete_une_trace_neuve_est_ouverte(monkeypatch):
    """Le planificateur, les scripts : aucune requête, donc aucun parent. La
    mesure ne doit surtout pas hériter de la trace d'une requête passée."""
    emetteur, captures = charger_emetteur(monkeypatch)
    appel_ia(emetteur)
    span, _ = captures[-1]
    assert len(span["traceId"]) == 32 and span["traceId"] != TRACE
    assert "parentSpanId" not in span


# ═══════════════════════════════════════════════════════════════════════════
#  Chaque appel part sous le jeton de son agent xSOM
# ═══════════════════════════════════════════════════════════════════════════
AGENTS = "matching=xsg_matching, ao=xsg_ao, matching/refusal=xsg_refus"


@pytest.mark.parametrize("route, jeton_attendu", [
    ("matching/score", "xsg_matching"),     # par domaine
    ("matching/refusal", "xsg_refus"),      # la route exacte l'emporte sur le domaine
    ("ao/draft", "xsg_ao"),
    ("assistant/chat", "xsg_defaut"),       # aucune correspondance → agent fourre-tout
    (None, "xsg_defaut"),
])
def test_chaque_appel_part_sous_le_jeton_de_son_agent(monkeypatch, route, jeton_attendu):
    """Dans xSOM, un agent EST un jeton de passerelle (poc-AI_guard,
    core/agents.py) : c'est le jeton utilisé à l'envoi qui range l'appel."""
    emetteur, captures = charger_emetteur(monkeypatch, xsom_agent_tokens=AGENTS)
    appel_ia(emetteur, route=route)
    assert captures[-1][1] == jeton_attendu


def test_sans_table_dagents_tout_part_sous_le_jeton_historique(monkeypatch):
    """Aucun changement tant que XSOM_AGENT_TOKENS n'est pas renseigné : le
    déploiement de ce code ne peut rien casser à lui seul."""
    emetteur, captures = charger_emetteur(monkeypatch)
    for route in ("matching/score", "ao/draft", "cv/vision"):
        appel_ia(emetteur, route=route)
    assert {jeton for _, jeton in captures} == {"xsg_defaut"}


def test_une_entree_mal_formee_ne_fait_pas_tomber_le_backend(monkeypatch):
    """Une faute de frappe dans .env est lue à l'import du module : si elle
    levait, le backend entier refuserait de démarrer."""
    emetteur, captures = charger_emetteur(
        monkeypatch, xsom_agent_tokens="matching=xsg_m,,=sans_cle, ao= ,cv sans_egal,cv=xsg_cv")
    assert emetteur._XSOM_AGENT_TOKENS == {"matching": "xsg_m", "cv": "xsg_cv"}


def test_sans_jeton_par_defaut_une_fonction_oubliee_nest_envoyee_nulle_part(monkeypatch):
    """Le cas que le jeton fourre-tout existe pour éviter, figé ici pour qu'on
    ne le découvre pas en production : sans lui, une fonction absente de la table
    ne part vers aucun agent."""
    emetteur, captures = charger_emetteur(
        monkeypatch, xsom_gateway_token=None, xsom_agent_tokens="matching=xsg_m")
    appel_ia(emetteur, route="cv/vision")
    assert captures[-1][1] is None


# ═══════════════════════════════════════════════════════════════════════════
#  De bout en bout, dans une vraie application FastAPI
# ═══════════════════════════════════════════════════════════════════════════
#  Les tests précédents jouent le middleware sur une application ASGI minimale.
#  Celui-ci ajoute ce qu'eux ne peuvent pas prouver : que Starlette transmet bien
#  le contexte jusqu'à une route SYNCHRONE, exécutée dans un fil du pool — c'est
#  là qu'une variable de contexte se perd quand quelqu'un ne la copie pas.
#  FastAPI n'est pas installé dans tous les environnements de développement ;
#  la CI l'installe, et c'est là que ce test fait foi.
@pytest.mark.parametrize("chemin", ["/synchrone", "/asynchrone"])
def test_de_bout_en_bout_dans_fastapi(monkeypatch, chemin):
    fastapi = pytest.importorskip("fastapi")
    testclient = pytest.importorskip("fastapi.testclient")
    monkeypatch.delenv("MIP_RUM_ENDPOINT", raising=False)
    emetteur, captures = charger_emetteur(monkeypatch)

    app = fastapi.FastAPI()
    app.add_middleware(mip_rum_middleware.MIPRumMiddleware)

    @app.get("/synchrone")
    def route_synchrone():
        appel_ia(emetteur, route="matching/score")
        return {"ok": True}

    @app.get("/asynchrone")
    async def route_asynchrone():
        appel_ia(emetteur, route="ao/draft")
        return {"ok": True}

    with testclient.TestClient(app) as client:
        reponse = client.get(chemin, headers={"traceparent": TRACEPARENT, "tracestate": TRACESTATE})
    assert reponse.status_code == 200
    span, _ = captures[-1]
    assert span["traceId"] == TRACE, f"{chemin} : le contexte ne parvient pas jusqu'à l'appel IA"
    assert attribut(span, "mip.session_id") == "session_42"
