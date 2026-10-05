"""Le contexte de trace de la requête HTTP en cours.

POURQUOI CE MODULE EXISTE
Un appel IA est presque toujours déclenché par une requête HTTP : un clic dans
l'interface → POST /aos/{id}/matching → plusieurs appels de modèle. La mesure de
chaque appel IA (mip_rum_ai.py) doit porter la trace de cette requête, pour être
rattachée au clic du navigateur qui l'a déclenchée — dans MIP pour le web, dans
xSOM AI Guard pour l'IA.

D'OÙ VIENT LE CONTEXTE
Depuis le 05/10/2026, de l'agent OpenTelemetry OFFICIEL (``opentelemetry-instrument``,
voir uti-backend.service), qui remplace l'ancien middleware maison. Son
instrumentation FastAPI lit l'en-tête W3C ``traceparent`` du navigateur, ouvre le
span serveur de la requête et le rend « courant » pour toute la durée du
traitement — dans la tâche asyncio comme dans les fils des routes synchrones.
Ce module ne fait que lire ce span courant.

Sans agent (tests, poste de développement), l'API OpenTelemetry ne fait rien :
aucun contexte, et l'émetteur IA retombe sur une trace neuve, comme hors requête.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from opentelemetry import trace

# Session MIP posée par le SDK navigateur dans tracestate : « mip=s:<id> ».
TRACESTATE_MIP = re.compile(r"(?:^|[,\s])mip=s:([A-Za-z0-9_-]{1,64})")


@dataclass(frozen=True)
class RequestTrace:
    trace_id: str              # celui du navigateur s'il a envoyé traceparent
    span_id: str               # le span serveur de CETTE requête
    session_id: Optional[str]  # session MIP (tracestate), si présente


def current() -> Optional[RequestTrace]:
    """Le contexte de la requête en cours, ou None hors requête (ou sans agent)."""
    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return None
    # Le span serveur hérite du tracestate du navigateur : la session y voyage.
    mip = ctx.trace_state.get("mip") if ctx.trace_state is not None else None
    m = TRACESTATE_MIP.search(f"mip={mip}") if mip else None
    return RequestTrace(
        trace_id=format(ctx.trace_id, "032x"),
        span_id=format(ctx.span_id, "016x"),
        session_id=m.group(1) if m else None,
    )
