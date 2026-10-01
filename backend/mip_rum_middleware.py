"""MIP RUM — middleware ASGI de tracing distribué (stdlib uniquement).

Lit le header W3C ``traceparent`` injecté par le SDK web MIP RUM, chronomètre la
requête, et expédie un span OTLP/HTTP JSON ``http.server`` vers l'ingestion MIP.
La corrélation front→back se fait par trace_id ; la session web (optionnelle)
voyage dans ``tracestate: mip=s:<session_id>``. Ce contexte est posé au DÉBUT de
chaque requête (request_trace.py) : les appels IA faits pendant la requête le
reprennent, et deviennent les enfants de son span http.server.

Activation par variables d'environnement — sans elles, passthrough total :
  MIP_RUM_ENDPOINT  ex. https://mip-rum-console.vercel.app/api/ingest/v1/traces
                    (l'ancienne edge function Supabase a été supprimée en
                    août 2026 : une valeur en *.supabase.co n'ingère plus rien)
  MIP_RUM_APP_ID    ex. gip-plateforme
  MIP_RUM_API_KEY   optionnel (clé d'app MIP RUM)
  MIP_RUM_IGNORE    routes exactes ignorées (défaut "/health,/docs,/openapi.json,/favicon.ico")

Usage FastAPI / Starlette :
  from mip_rum_middleware import MIPRumMiddleware
  app.add_middleware(MIPRumMiddleware)

Garanties : aucune exception ne remonte à l'app hôte ; ni corps, ni query
string, ni header métier collectés (route template + méthode + statut + durée) ;
batch mémoire borné (flush 5 s ou 20 spans, file coupée à 1000) ; envoi best
effort (timeout 3 s, échec silencieux) — un restart peut perdre la fenêtre de
flush courante, assumé.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import urllib.request

import request_trace

VERSION = "0.4.0"

_SEG_NUM = re.compile(r"^\d+$")
_SEG_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)
_SEG_HEX = re.compile(r"^[0-9a-f]{16,}$", re.I)

_DEFAULT_IGNORE = "/health,/docs,/openapi.json,/favicon.ico"


def _normalize(path: str) -> str:
    """Même convention que le SDK web : /partners/42 -> /partners/:id."""
    segs = [
        ":id" if (_SEG_NUM.match(s) or _SEG_UUID.match(s) or _SEG_HEX.match(s)) else s
        for s in path.split("/")
    ]
    return "/".join(segs) or "/"


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


def _post(endpoint: str, payload: dict) -> None:
    """POST OTLP bloquant (exécuté hors event loop, via run_in_executor)."""
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=3) as resp:
        resp.read()


class MIPRumMiddleware:
    """Middleware ASGI pur (pas de BaseHTTPMiddleware : zéro buffering du corps)."""

    def __init__(
        self,
        app,
        endpoint: str | None = None,
        app_id: str | None = None,
        api_key: str | None = None,
        service_name: str = "fastapi",
        ignore: str | None = None,
        flush_s: float = 5.0,
        batch_size: int = 20,
        queue_max: int = 1000,
    ) -> None:
        self.app = app
        self.endpoint = endpoint or os.environ.get("MIP_RUM_ENDPOINT")
        self.app_id = app_id or os.environ.get("MIP_RUM_APP_ID")
        self.api_key = api_key or os.environ.get("MIP_RUM_API_KEY")
        self.service_name = service_name
        self.ignore = set(
            s.strip()
            for s in (ignore or os.environ.get("MIP_RUM_IGNORE") or _DEFAULT_IGNORE).split(",")
            if s.strip()
        )
        self.flush_s = flush_s
        self.batch_size = batch_size
        self.queue_max = queue_max
        self.enabled = bool(self.endpoint and self.app_id)
        self._buf: list[dict] = []
        self._task: asyncio.Task | None = None

    # --- ASGI -----------------------------------------------------------------
    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)

        # Le contexte de trace est posé AVANT d'appeler l'application — et même
        # si l'envoi vers MIP est inactif. C'est lui qui rattache les appels IA
        # faits pendant cette requête au clic du navigateur qui l'a déclenchée
        # (request_trace.py). Il était auparavant calculé dans _record, APRÈS la
        # réponse : trop tard pour qu'un appel IA puisse s'y accrocher, et chaque
        # mesure IA partait avec une trace tirée au hasard.
        try:
            ctx = request_trace.from_headers(*self._trace_headers(scope))
            jeton = request_trace.set_current(ctx)
        except Exception:  # le tracing ne casse JAMAIS la requête hôte
            ctx = jeton = None
        try:
            if not self.enabled:
                return await self.app(scope, receive, send)

            status = {"code": 0}

            async def send_wrapper(message):
                if message.get("type") == "http.response.start":
                    status["code"] = int(message.get("status", 0))
                await send(message)

            start_ns = time.time_ns()
            t0 = time.perf_counter()
            try:
                await self.app(scope, receive, send_wrapper)
            finally:
                try:
                    self._record(scope, status["code"], t0, start_ns, ctx)
                except Exception:  # le tracing ne casse JAMAIS la requête hôte
                    pass
        finally:
            if jeton is not None:
                try:
                    request_trace.reset_current(jeton)
                except Exception:  # le tracing ne casse JAMAIS la requête hôte
                    pass

    @staticmethod
    def _trace_headers(scope) -> tuple:
        """(traceparent, tracestate) : les deux seuls en-têtes que ce middleware lise."""
        valeurs = {}
        for k, v in scope.get("headers") or []:
            if k in (b"traceparent", b"tracestate"):
                valeurs[k] = v.decode("latin1")
        return valeurs.get(b"traceparent"), valeurs.get(b"tracestate")

    # --- collecte ---------------------------------------------------------------
    def _record(self, scope, status_code: int, t0: float, start_ns: int, ctx=None) -> None:
        path = scope.get("path", "/")
        if path in self.ignore or scope.get("method") == "OPTIONS":
            return  # préflights CORS : jamais de span
        duration_ms = round((time.perf_counter() - t0) * 1000, 1)

        # Mêmes identifiants que ceux vus par les appels IA de cette requête :
        # c'est ce qui fait de ce span le PARENT de leurs mesures.
        if ctx is None:
            ctx = request_trace.from_headers(*self._trace_headers(scope))
        trace_id = ctx.trace_id
        parent_span_id = ctx.parent_span_id
        span_id = ctx.span_id
        session_id = ctx.session_id

        # route template FastAPI si le routing l'a posée, sinon path normalisé
        route = scope.get("route")
        route_path = getattr(route, "path_format", None) or getattr(route, "path", None)
        if not route_path:
            route_path = _normalize(path)

        attrs = [
            _kv("mip.trace_id", trace_id),
            _kv("mip.span_id", span_id),
            _kv("mip.route", route_path),
            _kv("http.url", path),
            _kv("http.method", str(scope.get("method", "GET")).upper()),
            _kv("http.status_code", int(status_code)),
            _kv("http.duration_ms", float(duration_ms)),
        ]
        if parent_span_id:
            attrs.append(_kv("mip.parent_span_id", parent_span_id))
        if session_id:
            attrs.append(_kv("mip.session_id", session_id))

        span = {
            "traceId": trace_id,
            "spanId": span_id,
            "name": "http.server",
            "kind": 2,
            "startTimeUnixNano": str(start_ns),
            "endTimeUnixNano": str(start_ns + int(duration_ms * 1_000_000)),
            "attributes": attrs,
        }
        if parent_span_id:
            span["parentSpanId"] = parent_span_id

        if len(self._buf) >= self.queue_max:
            del self._buf[: len(self._buf) - self.queue_max + 1]
        self._buf.append(span)

        loop = asyncio.get_running_loop()
        if self._task is None or self._task.done():
            self._task = loop.create_task(self._flush_loop())
        if len(self._buf) >= self.batch_size:
            asyncio.ensure_future(self._flush())

    # --- expédition ---------------------------------------------------------------
    def _otlp(self, batch: list[dict]) -> dict:
        res_attrs = [
            _kv("service.name", self.service_name),
            _kv("mip.app_id", self.app_id),
        ]
        if self.api_key:
            res_attrs.append(_kv("mip.api_key", self.api_key))
        return {
            "resourceSpans": [
                {
                    "resource": {"attributes": res_attrs},
                    "scopeSpans": [
                        {
                            "scope": {"name": "mip-rum-fastapi", "version": VERSION},
                            "spans": batch,
                        }
                    ],
                }
            ]
        }

    async def _flush(self) -> None:
        if not self._buf:
            return
        batch, self._buf = self._buf[:200], self._buf[200:]
        try:
            await asyncio.get_running_loop().run_in_executor(
                None, _post, self.endpoint, self._otlp(batch)
            )
        except Exception:
            pass  # best effort : pas de retry côté serveur (volume négligeable)

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self.flush_s)
            await self._flush()
