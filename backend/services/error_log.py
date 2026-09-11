"""
Journal d'erreurs en mémoire, consultable par l'admin (GET /admin/errors).

Objectif : rendre VISIBLES les dégradations que le backend gère en best-effort
(échecs LLM, SMTP, scheduler, 500 inattendus). Sans ce journal, l'app continue
de fonctionner en mode dégradé sans que personne ne le sache — le fil rouge de
l'audit pré-prod.

Ring buffer mono-processus (uvicorn mono-worker) : pas de dépendance, pas de
migration ; se vide au restart. journald reste la source complète (RUNBOOK §3),
ceci est la vue « dernières 200 erreurs » accessible depuis l'UI admin.

DEUX DESTINATIONS, ET POURQUOI IL EN FALLAIT UNE SECONDE
Le ring buffer ci-dessus ne survit à rien : il se vide à chaque redémarrage,
donc à chaque déploiement, et il est plafonné à 200 entrées. Une dégradation
qui se produit 400 fois dans la semaine y est indiscernable d'une qui s'est
produite deux fois, et une panne du mardi a disparu avant qu'on la regarde le
dimanche. Or ces événements sont exactement ce qu'il faut compter sur la durée :
un repli LLM silencieux, un envoi SMTP raté, un 500 inattendu.

Chaque événement part donc AUSSI en une ligne « UTI_EVT {json} » sur la sortie
standard, que systemd range dans journald — persistant, horodaté, et interrogeable
sur sept jours par deploy/revue_hebdo.sh (§6). Une ligne, du JSON, un préfixe
fixe : c'est ce qui rend `grep` suffisant et évite d'installer quoi que ce soit.

Rien ici ne doit jamais lever ni bloquer : journaliser une erreur ne peut pas
devenir une deuxième erreur.
"""
import json
import sys
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
        _vers_journald(evt)
    except Exception:  # noqa: BLE001
        pass


# Préfixe stable : il sert de point d'ancrage à `grep` dans revue_hebdo.sh.
# Le changer casse silencieusement le comptage hebdomadaire — d'où le test
# tests/test_journal_structure.py qui en fige la forme.
PREFIXE_JOURNAL = "UTI_EVT"


def _vers_journald(evt: dict) -> None:
    """Une ligne JSON par événement, sur stdout, capturée par journald.

    UNE seule ligne, toujours : json.dumps échappe les retours à la ligne d'un
    message multi-lignes. Sans cela, une trace d'erreur produirait vingt lignes
    dont dix-neuf sans préfixe — invisibles au comptage, et illisibles mêlées
    aux logs d'uvicorn.

    flush=True n'est pas une précaution de confort : systemd relie stdout à un
    tube, Python passe alors en tampon par blocs (4 ko), et les événements
    resteraient coincés en mémoire parfois des heures — précisément pendant
    l'incident qu'on essaie de suivre en direct.
    """
    try:
        print(f"{PREFIXE_JOURNAL} {json.dumps(evt, ensure_ascii=False)}", flush=True)
    except Exception:  # noqa: BLE001
        # stdout fermé (tests, worker en cours d'arrêt) : le ring buffer a déjà
        # l'événement, et journaliser ne doit jamais casser l'appelant.
        try:
            sys.stderr.write(f"{PREFIXE_JOURNAL} (non journalisé)\n")
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
