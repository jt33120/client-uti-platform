"""
Chaque appel IA doit être rattaché à la requête qui l'a déclenché — et à son agent.

POURQUOI CE FICHIER EXISTE

Jusqu'au 1er octobre 2026, la mesure de chaque appel IA (mip_rum_ai.py) partait
avec un identifiant de trace tiré au hasard : elle était orpheline. Depuis le
05/10/2026, le contexte de la requête vient de l'agent OpenTelemetry OFFICIEL
(qui remplace l'ancien middleware maison) : son instrumentation FastAPI lit le
traceparent du navigateur et rend courant le span serveur de la requête.

Et côté xSOM AI Guard, un agent EST un jeton de passerelle : chaque fonction IA
doit partir sous le jeton de son agent.

Ces tests exécutent le vrai SDK OpenTelemetry, la vraie instrumentation FastAPI
et le vrai émetteur, avec un exportateur en mémoire à la place du réseau. Ce
qu'ils défendent :

  * un appel IA fait pendant une requête porte SA trace, a SON span serveur pour
    parent, et hérite de SA session (routes synchrones comme asynchrones) ;
  * le span serveur part vers MIP avec le parent et la session du navigateur ;
  * hors requête, une trace neuve est ouverte (le planificateur n'hérite de rien) ;
  * chaque appel part sous le jeton de son agent, avec un défaut qui ne perd rien.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import request_trace  # noqa: E402

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
SPAN_NAVIGATEUR = "00f067aa0ba902b7"
TRACEPARENT = f"00-{TRACE}-{SPAN_NAVIGATEUR}-01"
TRACESTATE = "mip=s:session_42"

# Ce que l'agent installe au lancement : un fournisseur de traceurs global. Il ne
# se pose qu'une fois par processus ; l'exportateur en mémoire tient lieu de MIP.
EXPORT = InMemorySpanExporter()
_fournisseur = TracerProvider()
_fournisseur.add_span_processor(SimpleSpanProcessor(EXPORT))
trace.set_tracer_provider(_fournisseur)


# ═══════════════════════════════════════════════════════════════════════════
#  Outillage
# ═══════════════════════════════════════════════════════════════════════════
@pytest.fixture(autouse=True)
def export_vide():
    EXPORT.clear()
    yield
    EXPORT.clear()


def charger_emetteur(monkeypatch, **reglages):
    """Le vrai mip_rum_ai.py, chargé avec une configuration de pacotille.

    L'émetteur lit ses réglages à l'import (``from config import settings``), et
    le vrai config.py exige pydantic-settings et toute la configuration de
    production. On lui substitue un module ``config`` le temps du test, et on
    charge l'émetteur sous un nom privé : le module réel n'est jamais touché, et
    rien ne fuit vers les autres tests.

    ``_emit`` (l'envoi vers xSOM) est remplacé par une capture : on observe
    exactement le span et le jeton qui partiraient, sans thread ni réseau.
    """
    defauts = dict(
        mip_rum_app_id="gip-plateforme",
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


def application(emetteur, session_explicite=None):
    """Une vraie application FastAPI, instrumentée comme l'agent le fait."""
    fastapi = pytest.importorskip("fastapi")
    instrumentation = pytest.importorskip("opentelemetry.instrumentation.fastapi")
    app = fastapi.FastAPI()

    @app.get("/synchrone")
    def route_synchrone():
        appel_ia(emetteur, route="matching/score", session_id=session_explicite)
        return {"ok": True}

    @app.get("/asynchrone")
    async def route_asynchrone():
        appel_ia(emetteur, route="ao/draft", session_id=session_explicite)
        return {"ok": True}

    @app.get("/contexte")
    def route_contexte():
        ctx = request_trace.current()
        return {"trace_id": ctx.trace_id, "span_id": ctx.span_id, "session_id": ctx.session_id}

    instrumentation.FastAPIInstrumentor.instrument_app(app, tracer_provider=_fournisseur)
    return app


def requete(app, chemin, entetes=None):
    testclient = pytest.importorskip("fastapi.testclient")
    entetes = {"traceparent": TRACEPARENT, "tracestate": TRACESTATE} if entetes is None else entetes
    with testclient.TestClient(app) as client:
        reponse = client.get(chemin, headers=entetes)
    assert reponse.status_code == 200
    return reponse


def span_serveur():
    serveurs = [s for s in EXPORT.get_finished_spans() if s.kind == trace.SpanKind.SERVER]
    assert len(serveurs) == 1, f"{len(serveurs)} spans serveur exportés"
    return serveurs[0]


# ═══════════════════════════════════════════════════════════════════════════
#  Le contexte de trace, lu sur l'agent
# ═══════════════════════════════════════════════════════════════════════════
def test_hors_requete_aucun_contexte():
    assert request_trace.current() is None


def test_pendant_une_requete_le_contexte_est_celui_du_navigateur(monkeypatch):
    emetteur, _ = charger_emetteur(monkeypatch)
    ctx = requete(application(emetteur), "/contexte").json()
    assert ctx["trace_id"] == TRACE
    assert ctx["session_id"] == "session_42"
    assert ctx["span_id"] == format(span_serveur().context.span_id, "016x")


def test_le_span_serveur_part_vers_mip_avec_le_navigateur_pour_parent(monkeypatch):
    """Ce que l'ancien middleware envoyait, l'agent l'envoie : un span serveur par
    requête, enfant du span navigateur, avec la session dans le tracestate (que
    MIP lit : sessionFromTraceState)."""
    emetteur, _ = charger_emetteur(monkeypatch)
    requete(application(emetteur), "/contexte")
    serveur = span_serveur()
    assert format(serveur.context.trace_id, "032x") == TRACE
    assert format(serveur.parent.span_id, "016x") == SPAN_NAVIGATEUR
    assert serveur.context.trace_state.get("mip") == "s:session_42"
    assert serveur.attributes.get("http.route") == "/contexte"


def test_sans_traceparent_une_trace_neuve_est_ouverte(monkeypatch):
    """Une requête n'est jamais refusée pour sa trace : appel hors navigateur →
    trace neuve, sans parent ni session."""
    emetteur, _ = charger_emetteur(monkeypatch)
    ctx = requete(application(emetteur), "/contexte", entetes={}).json()
    assert len(ctx["trace_id"]) == 32 and ctx["trace_id"] != TRACE
    assert ctx["session_id"] is None
    assert span_serveur().parent is None


# ═══════════════════════════════════════════════════════════════════════════
#  L'appel IA reprend la trace de sa requête
# ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("chemin", ["/synchrone", "/asynchrone"])
def test_un_appel_ia_pendant_une_requete_porte_sa_trace(monkeypatch, chemin):
    """Synchrone : la route tourne dans un fil du pool, là où un contexte se perd
    quand personne ne le copie."""
    emetteur, captures = charger_emetteur(monkeypatch)
    requete(application(emetteur), chemin)
    serveur_id = format(span_serveur().context.span_id, "016x")

    span, _ = captures[-1]
    assert span["traceId"] == TRACE, f"{chemin} : la mesure IA est encore orpheline"
    assert span["parentSpanId"] == serveur_id, "la mesure IA n'a pas le span serveur de sa requête pour parent"
    assert attribut(span, "mip.session_id") == "session_42", "la mesure IA n'hérite pas de la session du navigateur"

    # Le même appel, vu par MIP : le span gen_ai exporté par l'agent.
    gen_ai = [s for s in EXPORT.get_finished_spans() if s.name == "gen_ai"]
    assert len(gen_ai) == 1
    assert format(gen_ai[0].context.span_id, "016x") == span["spanId"], "xSOM et MIP ne parlent pas du même appel"
    assert format(gen_ai[0].parent.span_id, "016x") == serveur_id
    assert gen_ai[0].attributes.get("gen_ai.request.model") == "mistral-small"


def test_une_session_explicite_garde_la_priorite(monkeypatch):
    """routers/assistant.py passe déjà sa session : le contexte ne doit pas
    l'écraser, seulement combler son absence."""
    emetteur, captures = charger_emetteur(monkeypatch)
    requete(application(emetteur, session_explicite="session_explicite"), "/synchrone")
    assert attribut(captures[-1][0], "mip.session_id") == "session_explicite"


def test_hors_requete_une_trace_neuve_est_ouverte(monkeypatch):
    """Le planificateur, les scripts : aucune requête, donc aucun parent. La
    mesure ne doit surtout pas hériter de la trace d'une requête passée."""
    emetteur, captures = charger_emetteur(monkeypatch)
    appel_ia(emetteur)
    span, _ = captures[-1]
    assert len(span["traceId"]) == 32 and span["traceId"] != TRACE
    assert "parentSpanId" not in span


def test_une_exception_passe_le_span_en_erreur(monkeypatch):
    emetteur, captures = charger_emetteur(monkeypatch)
    with pytest.raises(TimeoutError):
        with emetteur.record_ai_call(provider="openrouter", model="m", route="ao/draft"):
            raise TimeoutError()
    span, _ = captures[-1]
    assert attribut(span, "error.type") == "TimeoutError"
    assert span["status"]["code"] == 2
    gen_ai = [s for s in EXPORT.get_finished_spans() if s.name == "gen_ai"][-1]
    assert gen_ai.status.status_code == trace.StatusCode.ERROR


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


