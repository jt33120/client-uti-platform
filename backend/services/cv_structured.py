"""CV structuré canonique (format Groupement-IT) — P1 transparence.

Le CV structuré (anonymisé) devient la SOURCE DE VÉRITÉ partagée :
  * la vue « CV analysé » l'affiche proprement (template GRP-IT) ;
  * l'IA CITE des extraits de ce même JSON au scoring (via son texte aplati),
    donc le surlignage devient exact (plus de matching flou PDF/texte brut).

Construit une fois à l'upload (best-effort), stocké dans submissions.cv_structured,
backfillé à la demande. TOUT est résilient à l'absence de la colonne (migration
0002 non encore appliquée) : on retombe alors sur le CV brut, sans jamais casser.
"""
from typing import Optional

from services.supabase_client import supabase
from services import cv_harmonizer
from services.error_log import record as _record_err

# Passe à True quand la colonne submissions.cv_structured est détectée absente
# (migration 0002 non appliquée) : on cesse alors d'harmoniser en boucle (ni la
# vue ni l'upload ne pourraient stocker le résultat). Réinitialisé au redémarrage
# du process (donc à chaque déploiement, où la migration est censée être posée).
_STRUCTURED_DISABLED = False


def _looks_like_missing_column(err: Exception) -> bool:
    s = str(err).lower()
    return any(k in s for k in ("cv_structured", "column", "pgrst204", "42703", "schema cache"))


def flatten_structured(cv: Optional[dict]) -> str:
    """Aplati le CV structuré en texte, DANS L'ORDRE D'AFFICHAGE de la vue
    « CV analysé ». C'est ce texte que l'IA reçoit et cite → chaque extrait cité
    se retrouve tel quel dans un champ affiché (surlignage exact).

    Ne lève JAMAIS : tourne dans le chemin de scoring, un CV malformé ne doit
    pas faire échouer tout le run (repli sur le CV brut côté appelant)."""
    if not isinstance(cv, dict):
        return ""
    try:
        return _flatten(cv)
    except Exception:  # noqa: BLE001
        return ""


def _flatten(cv: dict) -> str:
    out: list[str] = []
    title = str(cv.get("title") or "").strip()
    if title:
        out.append(title)
    for s in (cv.get("synthese") or []):
        s = str(s).strip()
        if s:
            out.append(s)
    for e in (cv.get("experiences") or []):
        if not isinstance(e, dict):
            continue
        head = " — ".join([p for p in [str(e.get("company") or "").strip(), str(e.get("role") or "").strip()] if p])
        if head:
            out.append(head)
        ctx = str(e.get("context") or "").strip()
        if ctx:
            out.append(ctx)
        for m in (e.get("missions") or []):
            m = str(m).strip()
            if m:
                out.append(m)
        env = str(e.get("environment") or "").strip()
        if env:
            out.append(env)
    comp = cv.get("competences") or {}
    if isinstance(comp, dict):
        for k in ("metier", "fonctionnelles", "soft_skills", "techniques"):
            for x in (comp.get(k) or []):
                x = str(x).strip()
                if x:
                    out.append(x)
    for x in (cv.get("langues") or []):
        x = str(x).strip()
        if x:
            out.append(x)
    for x in (cv.get("formation") or []):
        x = str(x).strip()
        if x:
            out.append(x)
    return "\n".join(out)


def get_structured(submission_id: str) -> Optional[dict]:
    """Lit le CV structuré stocké (None si absent/colonne non migrée)."""
    global _STRUCTURED_DISABLED
    if _STRUCTURED_DISABLED or not submission_id:
        return None
    try:
        row = supabase.table("submissions").select("cv_structured").eq(
            "id", submission_id).single().execute().data
    except Exception as e:  # noqa: BLE001
        # Colonne absente (migration non appliquée) → on coupe la structuration
        # pour ne pas harmoniser en boucle inutilement.
        if _looks_like_missing_column(e):
            _STRUCTURED_DISABLED = True
        return None
    cv = (row or {}).get("cv_structured")
    return cv if isinstance(cv, dict) and (cv.get("title") or cv.get("experiences")) else None


def _persist(submission_id: str, cv: dict) -> None:
    """Écrit le CV structuré (best-effort — jamais bloquant, colonne peut manquer)."""
    try:
        supabase.table("submissions").update({"cv_structured": cv}).eq("id", submission_id).execute()
    except Exception as e:  # noqa: BLE001
        # Colonne non migrée / cache PostgREST périmé : on ne persiste pas, mais
        # l'appelant a quand même le CV (recalculé au besoin). Pas une panne.
        if _looks_like_missing_column(e):
            global _STRUCTURED_DISABLED
            _STRUCTURED_DISABLED = True
        _record_err("cv.structured.persist",
                    f"cv_structured non persisté pour la soumission {submission_id} "
                    f"(colonne absente ? appliquer migrations/0002) : {e}",
                    level="warning")


async def ensure_structured(
    submission_id: str, cv_text: Optional[str] = None, lang: str = "fr",
) -> Optional[dict]:
    """Retourne le CV structuré de la soumission : depuis la base si déjà là,
    sinon le génère (harmonize) et le persiste. Best-effort : retourne None si
    l'IA est indisponible ou si aucun texte de CV n'est exploitable."""
    cached = get_structured(submission_id)
    if cached:
        return cached
    # Colonne absente : inutile d'harmoniser, on ne pourrait pas stocker le résultat
    # (get_structured a déjà positionné le flag). Repli propre côté appelant.
    if _STRUCTURED_DISABLED:
        return None
    if not cv_harmonizer.is_available():
        return None
    if not (cv_text and cv_text.strip()):
        try:
            row = supabase.table("submissions").select("cv_text").eq(
                "id", submission_id).single().execute().data
            cv_text = (row or {}).get("cv_text")
        except Exception:
            cv_text = None
    if not (cv_text and len(cv_text.strip()) >= 50):
        return None
    try:
        cv = await cv_harmonizer.harmonize_cv(cv_text, lang if lang in ("fr", "en") else "fr")
    except Exception as e:  # noqa: BLE001
        _record_err("cv.structured.build",
                    f"Structuration du CV en échec pour la soumission {submission_id}",
                    exc=e, level="warning")
        return None
    if not cv:
        return None
    _persist(submission_id, cv)
    return cv


async def build_structured_bg(submission_id: str) -> None:
    """Tâche de fond (upload) : construit le CV structuré sans jamais lever."""
    try:
        await ensure_structured(submission_id)
    except Exception as e:  # noqa: BLE001
        _record_err("cv.structured.bg",
                    f"build_structured_bg échec pour {submission_id}", exc=e, level="warning")
