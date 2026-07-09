"""Registre d'usage IA — la source de vérité, non hallucinée, du coût des LLM.

Chaque appel LLM écrit UNE ligne dans ``public.ai_usage`` :
  * le **coût réel** renvoyé par OpenRouter (``resp.usage.cost`` obtenu en
    demandant ``extra_body=OR_USAGE``), donc identique au chiffre facturé —
    pas une estimation locale (``cost_source = "openrouter"``) ;
  * à défaut (Mistral, usage absent), une estimation tarifaire locale
    (``cost_source = "estimate"``), clairement étiquetée comme telle ;
  * l'``generation_id`` OpenRouter, pour un audit ponctuel via
    ``GET /api/v1/generation?id=…`` (réconciliation à la ligne près) ;
  * l'**attribution** : quel compte, quel système IA (extraction / scoring /
    draft / summary / assistant / harmonize), quel AO / consultant.

L'attribution circule via un ``contextvars.ContextVar`` posé au point d'entrée
requête (routeur / tâche de fond) — pas besoin de la propager à travers cinq
couches d'appels. L'écriture est *best-effort* et **non bloquante** (thread
démon) : le registre ne doit jamais ralentir ni faire échouer un appel métier,
ni si la table ``ai_usage`` n'existe pas encore (dégradation propre, comme
``matching_runner._insert_matchings``).
"""
from __future__ import annotations

import contextvars
import threading
from typing import Any, Optional

from services.supabase_client import supabase

# extra_body à passer à ``chat.completions.create`` sur le client OpenRouter :
# force OpenRouter à renvoyer le coût réel et les tokens détaillés dans
# ``resp.usage`` (``cost``, ``cost_details``, ``prompt_tokens_details``…).
OR_USAGE: dict = {"usage": {"include": True}}

# Contexte d'attribution de la requête courante (posé au point d'entrée).
_CTX: "contextvars.ContextVar[dict]" = contextvars.ContextVar("ai_ledger_ctx", default={})

# Colonnes du registre qui peuvent manquer si la migration n'est pas encore
# passée : on les retire une à une plutôt que d'échouer (cf. PGRST204).
_OPTIONAL_COLS = (
    "generation_id", "user_id", "user_email", "entity_type", "entity_id",
    "input_tokens", "output_tokens", "cached_tokens", "cost_source", "latency_ms",
)

# Tarifs de repli (USD / million de tokens) — UNIQUEMENT pour l'estimation quand
# le provider ne renvoie pas de coût (Mistral). L'ordre compte : premier
# sous-libellé trouvé dans le nom du modèle. OpenRouter renvoyant le coût réel,
# ces tarifs ne servent jamais pour les lignes ``cost_source = "openrouter"``.
_PRICING: tuple[tuple[str, float, float], ...] = (
    ("haiku", 1.00, 5.00),
    ("sonnet", 3.00, 15.00),
    ("opus", 15.00, 75.00),
    ("mistral-large", 2.00, 6.00),
    ("mistral", 0.20, 0.60),
)


def set_context(**fields: Any) -> "contextvars.Token":
    """Pose (fusionne) l'attribution pour la requête/tâche courante.

    Champs reconnus : ``user_id``, ``user_email``, ``entity_type``, ``entity_id``.
    Retourne un token — appeler ``reset_context(token)`` en fin de traitement si
    l'on veut restaurer l'état précédent (facultatif dans un handler éphémère)."""
    cur = dict(_CTX.get() or {})
    for k, v in fields.items():
        if v is not None:
            cur[k] = v
    return _CTX.set(cur)


def reset_context(token: "contextvars.Token") -> None:
    try:
        _CTX.reset(token)
    except Exception:
        pass


def _estimate(model: str, input_tokens: Optional[int], output_tokens: Optional[int]) -> Optional[float]:
    m = (model or "").lower()
    for needle, pin, pout in _PRICING:
        if needle in m:
            return ((input_tokens or 0) / 1_000_000) * pin + ((output_tokens or 0) / 1_000_000) * pout
    return None


def usage_from(resp: Any) -> dict:
    """Extrait tokens + coût réel d'une réponse OpenAI/OpenRouter. Tolérant :
    tout champ absent → None (jamais d'exception)."""
    out: dict = {"generation_id": None, "input_tokens": None, "output_tokens": None,
                 "cached_tokens": None, "cost": None}
    try:
        out["generation_id"] = getattr(resp, "id", None)
    except Exception:
        pass
    u = getattr(resp, "usage", None)
    if u is None:
        return out
    out["input_tokens"] = getattr(u, "prompt_tokens", None)
    out["output_tokens"] = getattr(u, "completion_tokens", None)
    # Coût réel OpenRouter (présent seulement avec extra_body=OR_USAGE).
    cost = getattr(u, "cost", None)
    if cost is None:
        # OpenRouter peut aussi l'exposer via cost_details.upstream_inference_cost
        cd = getattr(u, "cost_details", None)
        if isinstance(cd, dict):
            cost = cd.get("upstream_inference_cost") or cd.get("total_cost")
    out["cost"] = cost
    # Tokens de cache (prompt caching) si le provider les détaille.
    ptd = getattr(u, "prompt_tokens_details", None)
    if ptd is not None:
        out["cached_tokens"] = getattr(ptd, "cached_tokens", None) if not isinstance(ptd, dict) else ptd.get("cached_tokens")
    return out


def _insert(row: dict) -> None:
    """Insert résilient : retire les colonnes absentes une à une, no-op si la
    table n'existe pas encore. Jamais d'exception propagée."""
    payload = {k: v for k, v in row.items() if v is not None}
    droppable = set(_OPTIONAL_COLS)
    for _ in range(len(_OPTIONAL_COLS) + 1):
        try:
            supabase.table("ai_usage").insert(payload).execute()
            return
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            # Cherche une colonne optionnelle nommée dans l'erreur PostgREST.
            hit = next((c for c in droppable if c in msg and c in payload), None)
            if hit:
                payload.pop(hit, None)
                droppable.discard(hit)
                continue
            # Table absente / autre erreur : on abandonne silencieusement.
            return


def record(
    *,
    provider: str,
    model: str,
    operation: str,
    resp: Any = None,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    cost: Optional[float] = None,
    latency_ms: Optional[int] = None,
    user_id: Optional[str] = None,
    user_email: Optional[str] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
) -> None:
    """Enregistre un appel LLM au registre (best-effort, non bloquant).

    Si ``resp`` est fourni, tokens / coût / generation_id en sont extraits. Le
    coût réel OpenRouter prime ; sinon une estimation tarifaire locale est
    calculée et étiquetée ``estimate``. L'attribution manquante est complétée
    depuis le contexte de requête (``set_context``)."""
    ctx = _CTX.get() or {}
    generation_id = None
    cached_tokens = None
    if resp is not None:
        u = usage_from(resp)
        generation_id = u["generation_id"]
        cached_tokens = u["cached_tokens"]
        if input_tokens is None:
            input_tokens = u["input_tokens"]
        if output_tokens is None:
            output_tokens = u["output_tokens"]
        if cost is None:
            cost = u["cost"]

    prov = (provider or "").lower() or "unknown"
    if cost is not None:
        cost_source = "openrouter" if prov == "openrouter" else "provider"
    else:
        cost = _estimate(model, input_tokens, output_tokens)
        cost_source = "estimate" if cost is not None else "none"

    row = {
        "provider": prov,
        "model": model or None,
        "operation": operation or None,
        "generation_id": generation_id,
        "user_id": user_id or ctx.get("user_id"),
        "user_email": user_email or ctx.get("user_email"),
        "entity_type": entity_type or ctx.get("entity_type"),
        "entity_id": entity_id or ctx.get("entity_id"),
        "input_tokens": int(input_tokens) if input_tokens is not None else None,
        "output_tokens": int(output_tokens) if output_tokens is not None else None,
        "cached_tokens": int(cached_tokens) if cached_tokens is not None else None,
        "cost_usd": round(float(cost), 6) if cost is not None else None,
        "cost_source": cost_source,
        "latency_ms": int(latency_ms) if latency_ms is not None else None,
    }

    # Écriture fire-and-forget : ne bloque jamais le chemin de la requête.
    try:
        threading.Thread(target=_insert, args=(row,), daemon=True).start()
    except Exception:
        pass
