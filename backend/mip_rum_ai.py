"""Instrumentation des appels LLM → MIP RUM (spans OTel GenAI ``gen_ai``).

Émet UN span par appel LLM (provider / modèle / tokens / latence) vers le même
endpoint OTLP que ``mip_rum_middleware`` (mêmes MIP_RUM_ENDPOINT / APP_ID /
API_KEY). Best-effort, jamais bloquant, et **aucun contenu** de prompt/réponse :
seules des métadonnées voyagent. Inactif si l'endpoint/app_id ne sont pas
configurés (passthrough total).

Usage :
    from mip_rum_ai import record_ai_call
    with record_ai_call(provider="openrouter", model=model, route="ao/draft") as call:
        resp = await client.chat.completions.create(...)
        u = resp.usage
        call.usage(input_tokens=u.prompt_tokens, output_tokens=u.completion_tokens,
                   cost=getattr(u, "cost", None))
    # à la sortie du bloc : span gen_ai émis (latence = durée). En cas
    # d'exception, error.type est renseigné et le span est émis en statut erreur.
"""
from __future__ import annotations

import contextlib
import json
import secrets
import threading
import time
import urllib.request
from typing import Optional

from config import settings
# On réutilise l'encodeur d'attribut, le POST OTLP, la regex tracestate et la
# version du middleware existant (même format de session que http.server).
from mip_rum_middleware import _kv, _post, _TRACESTATE_MIP, VERSION

_ENDPOINT = settings.mip_rum_endpoint
_APP_ID = settings.mip_rum_app_id
_API_KEY = settings.mip_rum_api_key
_ENABLED = bool(_ENDPOINT and _APP_ID)

# xSOM AI Guard — deuxième destination (dual-emit). Auth par en-tête, donc pas de
# mip.api_key dans la ressource. Inactif tant que l'URL/token/app_id manquent.
_XSOM_URL = settings.xsom_ai_url
_XSOM_TOKEN = settings.xsom_gateway_token
_XSOM_ENABLED = bool(_XSOM_URL and _XSOM_TOKEN and _APP_ID)


def session_id_from_tracestate(tracestate: Optional[str]) -> Optional[str]:
    """Extrait le ``mip.session_id`` du header ``tracestate: mip=s:<id>`` (best-effort)."""
    if not tracestate:
        return None
    m = _TRACESTATE_MIP.search(tracestate)
    return m.group(1) if m else None


def _otlp(span: dict) -> dict:
    res = [_kv("service.name", "fastapi-ai"), _kv("mip.app_id", _APP_ID)]
    if _API_KEY:
        res.append(_kv("mip.api_key", _API_KEY))
    return {
        "resourceSpans": [{
            "resource": {"attributes": res},
            "scopeSpans": [{
                "scope": {"name": "mip-rum-ai", "version": VERSION},
                "spans": [span],
            }],
        }]
    }


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


def _post_xsom(payload: dict) -> None:
    """POST OTLP vers xSOM /v1/ai-traces, auth par en-tête (bloquant, hors loop)."""
    req = urllib.request.Request(
        _XSOM_URL.rstrip("/") + "/ai-traces",
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json", "X-Gateway-Token": _XSOM_TOKEN},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=3) as resp:  # noqa: S310 - fixed https endpoint
        resp.read()


def _emit(span: dict) -> None:
    """Envoi fire-and-forget (thread daemon) : ne bloque jamais l'appel métier.

    Dual-emit best-effort : MIP RUM et/ou xSOM selon ce qui est configuré. Chaque
    destination est isolée — l'échec de l'une n'empêche pas l'autre.
    """
    def _run():
        if _ENABLED:
            try:
                _post(_ENDPOINT, _otlp(span))
            except Exception:
                pass
        if _XSOM_ENABLED:
            try:
                _post_xsom(_otlp_xsom(span))
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
    if not (_ENABLED or _XSOM_ENABLED):
        yield call
        return

    start_ns = time.time_ns()
    try:
        yield call
    except Exception as e:  # noqa: BLE001
        call.error(type(e).__name__)
        raise
    finally:
        try:
            end_ns = time.time_ns()
            attrs = [
                _kv("gen_ai.system", str(provider or "").lower() or "unknown"),
                _kv("gen_ai.request.model", str(model or "")),
                _kv("gen_ai.operation.name", str(operation or "chat")),
            ]
            if route:
                attrs.append(_kv("mip.route", route))
            if session_id:
                attrs.append(_kv("mip.session_id", session_id))
            for k, v in call._attrs.items():
                attrs.append(_kv(k, v))
            span = {
                "traceId": secrets.token_hex(16),
                "spanId": secrets.token_hex(8),
                "name": "gen_ai",
                "kind": 3,  # CLIENT
                "startTimeUnixNano": str(start_ns),
                "endTimeUnixNano": str(end_ns),
                "attributes": attrs,
            }
            if call._err:
                span["attributes"].append(_kv("error.type", call._err))
                span["status"] = {"code": 2, "message": call._err}  # STATUS_CODE_ERROR
            elif call._refusal:
                # Refus modèle : error.type reconnu par MIP (refusal_rate), statut OK.
                span["attributes"].append(_kv("error.type", call._refusal))
            _emit(span)
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
