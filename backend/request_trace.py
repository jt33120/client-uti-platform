"""Le contexte de trace de la requête HTTP en cours.

POURQUOI CE MODULE EXISTE
Un appel IA est presque toujours déclenché par une requête HTTP : un clic dans
l'interface → POST /aos/{id}/matching → plusieurs appels de modèle. Jusqu'ici,
la mesure de chaque appel IA (mip_rum_ai.py) recevait un identifiant de trace
tiré au hasard : elle était orpheline. Impossible de la rattacher à la requête
qui l'avait déclenchée, ni au clic du navigateur qui avait déclenché la requête.

Le middleware (mip_rum_middleware.py) connaît pourtant ce contexte : il lit
l'en-tête W3C ``traceparent`` posé par le SDK du navigateur. Il le pose ici, au
DÉBUT de la requête ; l'émetteur IA le relit au moment d'émettre. Résultat : une
trace unique navigateur → backend → appel IA, et un identifiant commun aux deux
outils — MIP pour le web, xSOM AI Guard pour l'IA, qui conserve ce trace_id avec
chaque appel (usage_events).

POURQUOI UNE VARIABLE DE CONTEXTE, ET PAS UN PARAMÈTRE
Sinon, le contexte devrait traverser routeur → service → client LLM dans les neuf
fonctions IA. ``contextvars`` le fait circuler seul : dans la tâche asyncio de la
requête, dans les threads d'``asyncio.to_thread`` et de Starlette pour les routes
synchrones (tous deux copient le contexte), et dans les tâches créées pendant la
requête — ``asyncio.create_task`` copie le contexte à la création, donc un
matching lancé en arrière-plan reste rattaché au clic qui l'a lancé. C'est le
même mécanisme que l'attribution du registre ``ai_usage`` (services/ai_ledger.py).

Hors requête (planificateur, scripts), il n'y a pas de contexte : l'émetteur
retombe sur une trace neuve, comme avant.

Bibliothèque standard seulement : le middleware et l'émetteur IA partagent ce
contexte sans avoir à s'importer l'un l'autre.
"""
from __future__ import annotations

import contextvars
import re
import secrets
from dataclasses import dataclass
from typing import Optional

# W3C Trace Context : version-traceid-parentid-flags (version 00 seulement).
TRACEPARENT = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}$")
# Session MIP posée par le SDK navigateur dans tracestate : « mip=s:<id> ».
TRACESTATE_MIP = re.compile(r"(?:^|[,\s])mip=s:([A-Za-z0-9_-]{1,64})")


@dataclass(frozen=True)
class RequestTrace:
    trace_id: str                  # celui du navigateur s'il a envoyé traceparent
    span_id: str                   # le span http.server de CETTE requête
    parent_span_id: Optional[str]  # le span navigateur qui a émis la requête
    session_id: Optional[str]      # session MIP (tracestate), si présente


_current: contextvars.ContextVar[Optional[RequestTrace]] = contextvars.ContextVar(
    "uti_request_trace", default=None
)


def from_headers(traceparent: Optional[str], tracestate: Optional[str]) -> RequestTrace:
    """Le contexte d'une requête, à partir de ses en-têtes W3C.

    Sans ``traceparent`` valide (appel hors navigateur, en-tête malformé), une
    trace neuve est ouverte : une requête n'est jamais refusée pour sa trace.
    """
    m = TRACEPARENT.match(traceparent or "")
    s = TRACESTATE_MIP.search(tracestate or "")
    return RequestTrace(
        trace_id=m.group(1) if m else secrets.token_hex(16),
        span_id=secrets.token_hex(8),
        parent_span_id=m.group(2) if m else None,
        session_id=s.group(1) if s else None,
    )


def set_current(ctx: RequestTrace) -> contextvars.Token:
    return _current.set(ctx)


def reset_current(token: contextvars.Token) -> None:
    _current.reset(token)


def current() -> Optional[RequestTrace]:
    """Le contexte de la requête en cours, ou None hors requête."""
    return _current.get()
