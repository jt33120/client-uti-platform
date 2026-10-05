"""Instrumentation des appels LLM (spans OTel GenAI ``gen_ai``).

Émet UN span par appel LLM (provider / modèle / tokens / latence), vers deux
destinations :

* **MIP RUM**, par l'agent OpenTelemetry officiel (``opentelemetry-instrument``,
  voir uti-backend.service) : le span est créé avec l'API OpenTelemetry et part
  avec ceux du serveur, enfant du span de la requête en cours. Sans agent, l'API
  ne fait rien.
* **xSOM AI Guard**, par un envoi direct (``/v1/ai-traces``), sous le jeton de
  l'agent xSOM de la fonction IA. Inactif sans URL ni jeton.

Best-effort, jamais bloquant, et **aucun contenu** de prompt/réponse : seules des
métadonnées voyagent.

Usage :
    from mip_rum_ai import record_ai_call
    with record_ai_call(provider="openrouter", model=model, route="ao/draft") as call:
        resp = await client.chat.completions.create(...)
        u = resp.usage
        call.usage(input_tokens=u.prompt_tokens, output_tokens=u.completion_tokens,
                   cost=getattr(u, "cost", None))
    # à la sortie du bloc : span gen_ai émis (latence = durée). En cas
    # d'exception, error.type est renseigné et le span est émis en statut erreur.

Rattachement à la requête : pendant une requête HTTP, le span reprend son
trace_id et a pour parent son span serveur (request_trace.py) — d'où une trace
unique navigateur → backend → appel IA. Hors requête (planificateur), une trace
neuve est ouverte.

Rattachement à un agent xSOM : dans xSOM AI Guard, un agent EST un jeton de
passerelle. XSOM_AGENT_TOKENS associe chaque fonction IA (par sa route) au
jeton de son agent ; sans correspondance, XSOM_GATEWAY_TOKEN sert de défaut.
"""
from __future__ import annotations

import contextlib
import json
import secrets
import threading
import time
import urllib.request
from typing import Optional

from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

import request_trace
from config import settings

VERSION = "0.5.0"

# Sans agent, un traceur qui ne fait rien ; avec l'agent, celui qu'il a installé.
_tracer = trace.get_tracer("uti.mip_rum_ai", VERSION)

_APP_ID = settings.mip_rum_app_id

# xSOM AI Guard. Auth par en-tête, donc pas de clé MIP dans la ressource. Inactif
# tant que l'URL/token/app_id manquent.
_XSOM_URL = settings.xsom_ai_url
_XSOM_TOKEN = settings.xsom_gateway_token


def _parse_agent_tokens(raw: Optional[str]) -> dict:
    """« matching=xsg_…, ao=xsg_… » → {"matching": "xsg_…", "ao": "xsg_…"}.

    Une entrée mal formée est ignorée, jamais fatale : une faute de frappe dans
    .env ne doit pas empêcher le backend de démarrer.
    """
    tokens = {}
    for part in (raw or "").split(","):
        cle, sep, jeton = part.partition("=")
        cle, jeton = cle.strip(), jeton.strip()
        if sep and cle and jeton:
            tokens[cle] = jeton
    return tokens


_XSOM_AGENT_TOKENS = _parse_agent_tokens(settings.xsom_agent_tokens)
_XSOM_ENABLED = bool(_XSOM_URL and _APP_ID and (_XSOM_TOKEN or _XSOM_AGENT_TOKENS))


def _xsom_token_for(route: Optional[str]) -> Optional[str]:
    """Le jeton — donc l'AGENT xSOM — sous lequel ranger un appel IA.

    Ordre : la route exacte (« matching/score »), puis son domaine
    (« matching »), puis le jeton unique XSOM_GATEWAY_TOKEN. Ce dernier sert de
    fourre-tout : sans lui, une fonction IA oubliée dans XSOM_AGENT_TOKENS ne
    serait envoyée nulle part, et ses appels disparaîtraient de xSOM sans bruit.
    """
    r = (route or "").strip()
    if r and r in _XSOM_AGENT_TOKENS:
        return _XSOM_AGENT_TOKENS[r]
    domaine = r.split("/", 1)[0]
    if domaine and domaine in _XSOM_AGENT_TOKENS:
        return _XSOM_AGENT_TOKENS[domaine]
    return _XSOM_TOKEN or None


def session_id_from_tracestate(tracestate: Optional[str]) -> Optional[str]:
    """Extrait le ``mip.session_id`` du header ``tracestate: mip=s:<id>`` (best-effort)."""
    if not tracestate:
        return None
    m = request_trace.TRACESTATE_MIP.search(tracestate)
    return m.group(1) if m else None


def _kv(key: str, value) -> dict:
    if isinstance(value, bool):
        v = {"boolValue": value}
    elif isinstance(value, int):
        v = {"intValue": str(value)}
    elif isinstance(value, float):
        v = {"doubleValue": value}
    else:
        v = {"stringValue": str(value)}
    return {"key": key, "value": v}


def _otlp_xsom(span: dict) -> dict:
    # xSOM s'authentifie par en-tête (X-Gateway-Token) → pas de mip.api_key ici.
    res = [_kv("service.name", "fastapi-ai"), _kv("mip.app_id", _APP_ID)]
    return {
        "resourceSpans": [{
            "resource": {"attributes": res},
            "scopeSpans": [{
                "scope": {"name": "mip-rum-ai", "version": VERSION},
                "spans": [span],
            }],
        }]
    }


def _post_xsom(payload: dict, token: str) -> None:
    """POST OTLP vers xSOM /v1/ai-traces, auth par en-tête (bloquant, hors loop).

    ``token`` désigne l'agent xSOM qui reçoit l'appel (cf. _xsom_token_for)."""
    req = urllib.request.Request(
        _XSOM_URL.rstrip("/") + "/ai-traces",
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json", "X-Gateway-Token": token},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=3) as resp:  # noqa: S310 - fixed https endpoint
        resp.read()


def _emit(span: dict, xsom_token: Optional[str] = None) -> None:
    """Envoi vers xSOM, fire-and-forget (thread daemon) : ne bloque jamais l'appel
    métier. MIP, lui, reçoit le span par l'agent OpenTelemetry."""
    if not (_XSOM_ENABLED and xsom_token):
        return

    def _run():
        try:
            _post_xsom(_otlp_xsom(span), xsom_token)
        except Exception:
            pass
    try:
        threading.Thread(target=_run, daemon=True).start()
    except Exception:
        pass


class _Call:
    def __init__(self):
        self._attrs: dict = {}
        self._err: Optional[str] = None
        self._refusal: Optional[str] = None

    def refusal(self, kind: str = "content_filter"):
        """Marque l'appel comme REFUS modèle (filtre de contenu / garde-fou).

        Distinct d'une exception : on renseigne ``error.type`` avec un motif que
        MIP reconnaît (refus|content_filter|guardrail|safety|moderation → alimente
        ``refusal_rate``) SANS passer le span en statut erreur — un refus est un
        comportement du modèle, pas une panne infra (ne gonfle pas le taux
        d'erreur technique)."""
        self._refusal = str(kind or "content_filter")

    def usage(self, input_tokens=None, output_tokens=None, cost=None):
        if input_tokens is not None:
            try:
                self._attrs["gen_ai.usage.input_tokens"] = int(input_tokens)
            except (TypeError, ValueError):
                pass
        if output_tokens is not None:
            try:
                self._attrs["gen_ai.usage.output_tokens"] = int(output_tokens)
            except (TypeError, ValueError):
                pass
        if cost is not None:
            try:
                self._attrs["gen_ai.usage.cost"] = float(cost)
            except (TypeError, ValueError):
                pass

    def error(self, err_type):
        self._err = str(err_type)

    def set(self, **attrs):
        self._attrs.update(attrs)


@contextlib.contextmanager
def record_ai_call(*, provider: str, model: str, operation: str = "chat",
                   route: Optional[str] = None, session_id: Optional[str] = None):
    """Chronomètre un appel LLM et émet un span ``gen_ai`` à la sortie du bloc."""
    call = _Call()
    # Le span n'est PAS rendu courant : un appel IA ne devient pas le parent de ce
    # que le code métier fait ensuite. Son parent est le span de la requête.
    otel_span = _tracer.start_span("gen_ai", kind=SpanKind.CLIENT)
    start_ns = time.time_ns()
    try:
        yield call
    except Exception as e:  # noqa: BLE001
        call.error(type(e).__name__)
        raise
    finally:
        try:
            end_ns = time.time_ns()
            ctx = request_trace.current()
            if not session_id and ctx is not None:
                session_id = ctx.session_id
            attrs = {
                "gen_ai.system": str(provider or "").lower() or "unknown",
                "gen_ai.request.model": str(model or ""),
                "gen_ai.operation.name": str(operation or "chat"),
            }
            if route:
                attrs["mip.route"] = route
            if session_id:
                attrs["mip.session_id"] = session_id
            attrs.update(call._attrs)
            if call._err:
                attrs["error.type"] = call._err
            elif call._refusal:
                # Refus modèle : error.type reconnu par MIP (refusal_rate), statut OK.
                attrs["error.type"] = call._refusal

            otel_span.set_attributes(attrs)
            if call._err:
                otel_span.set_status(Status(StatusCode.ERROR, call._err))
            otel_span.end(end_time=end_ns)

            # Les identifiants que l'agent a donnés au span, pour qu'xSOM et MIP
            # parlent du même appel ; sans agent, une trace neuve.
            sc = otel_span.get_span_context()
            span = {
                "traceId": format(sc.trace_id, "032x") if sc.is_valid else secrets.token_hex(16),
                "spanId": format(sc.span_id, "016x") if sc.is_valid else secrets.token_hex(8),
                "name": "gen_ai",
                "kind": 3,  # CLIENT
                "startTimeUnixNano": str(start_ns),
                "endTimeUnixNano": str(end_ns),
                "attributes": [_kv(k, v) for k, v in attrs.items()],
            }
            if ctx is not None:
                span["parentSpanId"] = ctx.span_id
            if call._err:
                span["status"] = {"code": 2, "message": call._err}  # STATUS_CODE_ERROR
            _emit(span, _xsom_token_for(route))
        except Exception:
            pass


def refusal_kind(resp) -> Optional[str]:
    """Détecte un REFUS modèle dans une réponse type OpenAI/OpenRouter (SDK).

    Renvoie un motif reconnu par MIP (``content_filter`` | ``refusal``) ou None :
    - ``finish_reason == 'content_filter'`` → filtre de contenu du fournisseur ;
    - ``message.refusal`` non vide → refus structuré (format OpenAI).
    Best-effort et défensif : toute forme inattendue → None (jamais d'exception)."""
    try:
        choice = (getattr(resp, "choices", None) or [None])[0]
        if choice is None:
            return None
        fr = str(getattr(choice, "finish_reason", "") or "").lower().replace("-", "_")
        if fr == "content_filter":
            return "content_filter"
        msg = getattr(choice, "message", None)
        ref = getattr(msg, "refusal", None) if msg is not None else None
        if ref and str(ref).strip():
            return "refusal"
    except Exception:
        return None
    return None


def flag_refusal(call, resp) -> Optional[str]:
    """Marque ``call`` comme refus si ``resp`` en est un. Renvoie le motif ou None.
    À appeler dans le bloc ``with record_ai_call(...) as call`` après la réponse."""
    kind = refusal_kind(resp)
    if kind and call is not None:
        try:
            call.refusal(kind)
        except Exception:
            pass
    return kind
