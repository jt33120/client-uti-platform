"""CV structuré canonique (format Groupement-IT) — P1 transparence.

Le CV structuré (anonymisé) devient la SOURCE DE VÉRITÉ partagée :
  * la vue « CV analysé » l'affiche proprement (template GRP-IT) ;
  * l'IA CITE des extraits de ce même JSON au scoring (via son texte aplati),
    donc le surlignage devient exact (plus de matching flou PDF/texte brut).

Construit une fois à l'upload (best-effort), stocké dans submissions.cv_structured,
backfillé à la demande. TOUT est résilient à l'absence de la colonne (migration
0002 non encore appliquée) : on retombe alors sur le CV brut, sans jamais casser.
"""
import asyncio
from typing import Optional

from services.supabase_client import supabase
from services import cv_harmonizer, cv_vision, storage
from services.error_log import record as _record_err

# Borne les analyses vision (Sonnet, coûteuses) concurrentes, tous appelants
# confondus (upload, vue à la demande, backfill). Plafond dur de durée pour ne
# jamais bloquer un re-score derrière un fournisseur lent.
_VISION_SEM = asyncio.Semaphore(2)
_VISION_DEADLINE = 90  # secondes

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


def _is_pdf(filename: Optional[str], cv_url: Optional[str]) -> bool:
    for s in (filename, cv_url):
        if s and s.lower().split("?", 1)[0].rstrip().endswith(".pdf"):
            return True
    return False


async def _try_vision(cv_url: Optional[str], filename: Optional[str],
                      name: Optional[str] = None) -> Optional[dict]:
    """Analyse VISION du PDF : rend les pages en image → un modèle multimodal lit
    étoiles/jauges/graphiques/scanné. None si indisponible / non-PDF / échec / timeout.
    Le téléchargement et la rasterisation (bloquants) sont déportés hors de l'event
    loop ; le tout est borné en concurrence et en durée. `name` sert à masquer
    l'identité (photo + nom/contacts) sur l'image avant envoi au fournisseur."""
    if not cv_vision.is_available() or not cv_url or not _is_pdf(filename, cv_url):
        return None
    try:
        async with _VISION_SEM:
            data = await asyncio.to_thread(
                storage.download, "cvs", storage._object_path("cvs", cv_url))
            images = await asyncio.to_thread(cv_vision.render_pdf_images, data, name)
            if not images:
                return None
            return await asyncio.wait_for(
                cv_vision.extract_structured_vision(images), timeout=_VISION_DEADLINE)
    except Exception as e:  # noqa: BLE001  (inclut asyncio.TimeoutError)
        _record_err("cv.structured.vision", "Analyse vision du CV en échec/expirée",
                    exc=e, level="warning")
        return None


async def ensure_structured(
    submission_id: str, cv_text: Optional[str] = None, lang: str = "fr",
) -> Optional[dict]:
    """Retourne le CV structuré (anonymisé) de la soumission : depuis la base si
    déjà là, sinon le génère et le persiste. Deux voies, la meilleure d'abord :
      1. VISION — lit les pages du PDF RENDUES EN IMAGE (voit étoiles/jauges/
         graphiques/scanné) → structuré enrichi des niveaux visuels ;
      2. TEXTE — harmonisation du texte extrait (repli).
    Best-effort : None si aucune voie n'aboutit."""
    cached = get_structured(submission_id)
    if cached:
        return cached
    # Colonne absente : inutile de générer, on ne pourrait pas stocker le résultat
    # (get_structured a déjà positionné le flag). Repli propre côté appelant.
    if _STRUCTURED_DISABLED:
        return None
    # Texte + URL du CV + nom (pour masquer l'identité sur l'image) en une lecture.
    try:
        row = supabase.table("submissions").select(
            "cv_text, cv_url, cv_filename, consultants(name)").eq(
            "id", submission_id).single().execute().data or {}
    except Exception:
        row = {}
    cv_text = cv_text or row.get("cv_text")
    name = (row.get("consultants") or {}).get("name")
    lang = lang if lang in ("fr", "en") else "fr"

    # 1) VISION (voit ce que le texte seul manque). Identité masquée avant envoi.
    cv = await _try_vision(row.get("cv_url"), row.get("cv_filename"), name)

    # 2) Repli TEXTE (harmonisation) si la vision n'a rien produit.
    if not cv and cv_harmonizer.is_available() and cv_text and len(cv_text.strip()) >= 50:
        try:
            cv = await cv_harmonizer.harmonize_cv(cv_text, lang)
        except Exception as e:  # noqa: BLE001
            _record_err("cv.structured.build",
                        f"Structuration texte du CV en échec pour la soumission {submission_id}",
                        exc=e, level="warning")
            cv = None

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
