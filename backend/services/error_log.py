"""
Journal d'erreurs en mémoire, consultable par l'admin (GET /admin/errors).

Objectif : rendre VISIBLES les dégradations que le backend gère en best-effort
(échecs LLM, SMTP, scheduler, 500 inattendus). Sans ce journal, l'app continue
de fonctionner en mode dégradé sans que personne ne le sache — le fil rouge de
l'audit pré-prod.

Ring buffer mono-processus (uvicorn mono-worker) : pas de dépendance, pas de
migration ; se vide au restart. journald reste la source complète (RUNBOOK §3),
ceci est la vue « dernières 200 erreurs » accessible depuis l'UI admin.
"""
import threading
import traceback
from collections import deque
from datetime import datetime, timezone
from typing import Optional

_MAX_EVENTS = 200
_EVENTS: deque = deque(maxlen=_MAX_EVENTS)
_LOCK = threading.Lock()


def record(
    source: str,
    message: str,
    *,
    level: str = "error",
    path: Optional[str] = None,
    exc: Optional[BaseException] = None,
) -> None:
    """Consigne un événement. Ne lève JAMAIS (le logging ne casse pas l'app).

    source : composant émetteur ("http", "scheduler", "llm.extraction", "smtp"…)
    level  : "error" (panne) ou "warning" (dégradation maîtrisée / fallback).
    path   : route HTTP ou identifiant de contexte, si pertinent.
    exc    : exception d'origine — seule la classe + le message sont conservés
             (la stack complète part dans journald via print, pas ici).
    """
    try:
        if exc is not None:
            message = f"{message} — {type(exc).__name__}: {exc}"
        evt = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": level if level in ("error", "warning") else "error",
            "source": source,
            "message": str(message)[:500],
            "path": path,
        }
        with _LOCK:
            _EVENTS.append(evt)
    except Exception:  # noqa: BLE001
        pass


def record_exception(source: str, message: str, exc: BaseException, *, path: Optional[str] = None) -> None:
    """Variante pratique : consigne + imprime la stack complète dans journald."""
    record(source, message, exc=exc, path=path)
    try:
        print(f"[{source.upper()}] {message}: {exc}\n{traceback.format_exc()}")
    except Exception:  # noqa: BLE001
        pass


def recent(limit: int = 100, level: Optional[str] = None) -> list:
    """Les derniers événements, du plus récent au plus ancien."""
    with _LOCK:
        events = list(_EVENTS)
    if level in ("error", "warning"):
        events = [e for e in events if e["level"] == level]
    return list(reversed(events))[: max(1, min(limit, _MAX_EVENTS))]
