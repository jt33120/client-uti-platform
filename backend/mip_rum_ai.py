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
import secrets
import threading
import time
from typing import Optional

from config import settings
# On réutilise l'encodeur d'attribut, le POST OTLP et la version du middleware.
from mip_rum_middleware import _kv, _post, VERSION

_ENDPOINT = settings.mip_rum_endpoint
_APP_ID = settings.mip_rum_app_id
_API_KEY = settings.mip_rum_api_key
_ENABLED = bool(_ENDPOINT and _APP_ID)


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


def _emit(span: dict) -> None:
    """Envoi fire-and-forget (thread daemon) : ne bloque jamais l'appel métier."""
    def _run():
        try:
            _post(_ENDPOINT, _otlp(span))
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
    if not _ENABLED:
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
            _emit(span)
        except Exception:
            pass
