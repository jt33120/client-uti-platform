"""Analyse VISION du CV — l'IA lit les pages RENDUES EN IMAGE.

Le pipeline texte (pdfplumber/OCR) est aveugle à tout ce qui n'est pas du texte :
jauges/étoiles de compétences, graphiques, encadrés, mises en page multi-colonnes,
CV scannés. Ici on rasterise chaque page du CV et on la donne à un modèle
multimodal (Claude via OpenRouter) qui la LIT comme un humain — il voit les
niveaux de maîtrise dessinés (★★★★☆, barres, %) et les restitue dans le CV
structuré. Résultat : le structuré (source de vérité du scoring, de l'affichage
et du surlignage) porte enfin ces signaux visuels.

100 % best-effort : si PyMuPDF n'est pas installé, si le modèle vision n'est pas
disponible, ou si le fichier n'est pas un PDF → retourne None, l'appelant retombe
sur l'extraction texte. Sortie ANONYMISÉE (jamais de nom/contact), même schéma
que cv_harmonizer.
"""
import base64
import io
import re
from typing import Optional

from openai import AsyncOpenAI
from config import settings
from mip_rum_ai import record_ai_call
from services import ai_ledger
from services.cv_harmonizer import _extract_json, _sanitize, _SCHEMA
from services.error_log import record as _record_err

_client: Optional[AsyncOpenAI] = (
    AsyncOpenAI(api_key=settings.openrouter_key, base_url="https://openrouter.ai/api/v1", timeout=90, max_retries=1)
    if settings.openrouter_key else None
)
VISION_MODEL = settings.vision_model

# Bornes : Claude sous-échantillonne au-delà de ~1568 px de côté long → inutile de
# rendre plus fin. On vise ~1540 px (net pour lire des étoiles/petites jauges) et on
# borne le nombre de pages (coût/tokens) — un CV tient quasi toujours en ≤ 8 pages.
_RENDER_LONG_EDGE = 1540
_MAX_PAGES = 12  # lit les CV longs en entier (densité)


def is_available() -> bool:
    return _client is not None and settings.vision_enabled


# Rédaction de l'identité AVANT envoi de l'image au fournisseur : l'image (contrairement
# au texte) ne peut pas être pseudonymisée en aval, donc on masque photo + nom + e-mail
# + téléphone sur la page rendue. Best-effort et ciblé (on épargne les graphiques/étoiles).
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Téléphone FR / international assez STRICT (structure 5×2 chiffres) pour ne pas
# masquer par erreur des nombres utiles (« 15 ans d'expérience »).
_PHONE_RE = re.compile(r"(?:(?:\+|00)\d{1,3}[\s.\-]?)?0?[1-9](?:[\s.\-]?\d{2}){4}")


def _redact_identity(page, name: Optional[str], first_page: bool) -> None:
    """Masque (rectangles blancs) l'identité sur une page : nom + e-mails + téléphones
    (couche texte) et, sur la 1re page, la PHOTO (image embarquée dans le haut de page).
    Best-effort — ne lève jamais ; en cas d'échec la page est rendue telle quelle."""
    try:
        targets: set[str] = set()
        if name and len(name.strip()) >= 3:
            targets.add(name.strip())
            for part in name.split():
                if len(part) >= 3:
                    targets.add(part)
        txt = page.get_text() or ""
        for m in _EMAIL_RE.findall(txt):
            targets.add(m)
        for m in _PHONE_RE.findall(txt):
            if len(re.sub(r"\D", "", m)) >= 9:
                targets.add(m.strip())

        annots = 0
        for t in targets:
            for rect in (page.search_for(t) or [])[:8]:
                page.add_redact_annot(rect, fill=(1, 1, 1))
                annots += 1
        if first_page:
            ph, pw = page.rect.height or 1, page.rect.width or 1
            for img in (page.get_images(full=True) or []):
                try:
                    for rect in (page.get_image_rects(img[0]) or []):
                        # image assez grande ET dans le haut de page → probable photo.
                        if rect.y0 < ph * 0.42 and (rect.width * rect.height) > pw * ph * 0.008:
                            page.add_redact_annot(rect, fill=(1, 1, 1))
                            annots += 1
                except Exception:
                    continue
        if annots:
            page.apply_redactions()
    except Exception:
        pass


def render_pdf_images(file_bytes: bytes, name: Optional[str] = None,
                      max_pages: int = _MAX_PAGES) -> list[bytes]:
    """Rasterise les pages d'un PDF en PNG (via PyMuPDF), APRÈS rédaction de
    l'identité (photo + nom/contacts). [] si PyMuPDF absent ou fichier illisible
    (→ l'appelant retombe sur le texte). Best-effort, ne lève jamais."""
    try:
        import fitz  # PyMuPDF (pip pur, déjà utilisé par le repli OCR)
    except ImportError:
        return []  # PyMuPDF absent → pas d'images (repli texte côté appelant)
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
    except Exception:
        return []
    out: list[bytes] = []
    try:
        # enumerate(doc) charge chaque page à l'itération : un objet-page corrompu
        # (PDF tronqué / re-scanné) lève ICI, hors du try interne → on borne tout
        # dans un try/except pour garantir « ne lève jamais » (repli texte assuré).
        for i in range(min(doc.page_count, max_pages)):
            try:
                page = doc.load_page(i)
                _redact_identity(page, name, first_page=(i == 0))
                rect = page.rect
                long_edge = max(rect.width, rect.height) or 1
                # PAS de plancher à 1.0 : les grandes pages doivent être RÉDUITES
                # (sinon pixmap géant = risque mémoire). On borne le côté long à
                # ~1540 px ; agrandissement ×3 max pour les petites pages.
                zoom = min(3.0, max(0.1, _RENDER_LONG_EDGE / long_edge))
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
                out.append(pix.tobytes("png"))
            except Exception:
                continue
    except Exception:
        pass  # itération/chargement de page en échec : on renvoie ce qu'on a
    finally:
        try:
            doc.close()
        except Exception:
            pass
    return out


_VISION_SYSTEM = (
    "Tu analyses un CV présenté sous forme d'IMAGES (une par page). Tu le LIS comme "
    "un recruteur humain : tu vois la mise en page, les graphiques, les encadrés, et "
    "surtout les NIVEAUX DE MAÎTRISE DESSINÉS (étoiles ★★★★☆, barres de progression, "
    "pourcentages, pastilles, jauges) que le texte brut ne contient pas.\n\n"
    "Tu restructures le contenu EXISTANT au format standard du Groupement-IT, en "
    "français, sans jamais inventer d'expérience ou de compétence absente.\n"
    "RÈGLE ABSOLUE — anonymisation : n'inclus JAMAIS nom, prénom, e-mail, téléphone, "
    "adresse ni photo. Le document commence par l'intitulé de poste.\n"
    "SIGNAUX VISUELS — quand une compétence est accompagnée d'un niveau DESSINÉ "
    "(étoiles, barre, %, pastilles), APPENDS ce niveau à la compétence sous la forme "
    "« Compétence — niveau: expert (4/5) » (mappe : plein/élevé→expert, ~3/5→confirmé, "
    "~2/5→intermédiaire, faible→notions). Ne fabrique JAMAIS un niveau si rien n'est "
    "dessiné. Restitue aussi tout élément visuel porteur de sens (schéma de "
    "compétences, frise, encadré « points clés ») dans la synthèse.\n"
    "Réponds STRICTEMENT en JSON valide conforme à ce schéma (sans texte autour) :\n"
    + _SCHEMA
)


async def extract_structured_vision(images: list[bytes], lang: str = "fr") -> Optional[dict]:
    """Extrait le CV structuré (anonymisé, enrichi des niveaux visuels) à partir des
    images de pages. None si vision indisponible / aucune image / échec."""
    if not _client or not images:
        return None
    content: list[dict] = [{
        "type": "text",
        "text": ("Analyse ce CV (images ci-dessous, dans l'ordre des pages) et renvoie "
                 "le JSON structuré demandé. Lis attentivement les niveaux de compétences "
                 "dessinés (étoiles/barres/%)."),
    }]
    for img in images[:_MAX_PAGES]:
        b64 = base64.b64encode(img).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})

    try:
        with record_ai_call(provider="openrouter", model=VISION_MODEL, operation="chat", route="cv/vision") as _call:
            resp = await _client.chat.completions.create(
                model=VISION_MODEL,
                temperature=0,  # reproductible + limite l'invention sur images ambiguës
                max_tokens=8000,
                messages=[
                    {"role": "system", "content": _VISION_SYSTEM},
                    {"role": "user", "content": content},
                ],
                extra_body=ai_ledger.OR_USAGE,
            )
            _u = getattr(resp, "usage", None)
            if _u:
                _call.usage(input_tokens=getattr(_u, "prompt_tokens", None),
                            output_tokens=getattr(_u, "completion_tokens", None),
                            cost=getattr(_u, "cost", None))
        ai_ledger.record(provider="openrouter", model=VISION_MODEL, operation="cv_vision",
                         resp=resp, entity_type="consultant")
    except Exception as e:  # noqa: BLE001
        _record_err("cv.vision", f"Analyse vision du CV en échec ({VISION_MODEL})", exc=e, level="warning")
        return None

    choice = (getattr(resp, "choices", None) or [None])[0]
    if choice is None:
        return None
    if getattr(choice, "finish_reason", None) == "length":
        # JSON coupé par max_tokens : le parse échouera probablement → repli texte.
        _record_err("cv.vision", "Réponse vision tronquée (max_tokens) — repli extraction texte",
                    level="warning")
    data = _extract_json(getattr(getattr(choice, "message", None), "content", None) or "")
    if not data:
        return None
    out = _sanitize(data)
    # Un JSON valide mais vide (refus/hallucination) n'est pas un succès.
    if out.get("title") or out.get("experiences"):
        return out
    return None
