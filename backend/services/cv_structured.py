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


def flatten_structured(cv: Optional[dict]) -> str:
    """Aplati le CV structuré en texte, DANS L'ORDRE D'AFFICHAGE de la vue
    « CV analysé ». C'est ce texte que l'IA reçoit et cite → chaque extrait cité
    se retrouve tel quel dans un champ affiché (surlignage exact)."""
    if not isinstance(cv, dict):
        return ""
    out: list[str] = []
    title = (cv.get("title") or "").strip()
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
    if not submission_id:
        return None
    try:
        row = supabase.table("submissions").select("cv_structured").eq(
            "id", submission_id).single().execute().data
    except Exception:
        return None  # colonne absente (migration non appliquée) ou soumission introuvable
    cv = (row or {}).get("cv_structured")
    return cv if isinstance(cv, dict) and (cv.get("title") or cv.get("experiences")) else None


def _persist(submission_id: str, cv: dict) -> None:
    """Écrit le CV structuré (best-effort — jamais bloquant, colonne peut manquer)."""
    try:
        supabase.table("submissions").update({"cv_structured": cv}).eq("id", submission_id).execute()
    except Exception as e:  # noqa: BLE001
        # Colonne non migrée / cache PostgREST périmé : on ne persiste pas, mais
        # l'appelant a quand même le CV (recalculé au besoin). Pas une panne.
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
