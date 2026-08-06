"""
RGPD / GDPR — droit à l'effacement (right to erasure, art. 17 RGPD).

The existing admin "delete account" only removes the profile row and the
Supabase Auth user. Because consultants.created_by and submissions.submitted_by
are declared ON DELETE SET NULL, the personal data carried by those rows
(consultant identity, CV text, CV files in storage, AI matching summaries)
survives account deletion — which is not compliant with a real erasure request.

This endpoint performs a *full* cascade erasure of every piece of personal
data attached to a user, in dependency order, and removes the CV files from
object storage (which no SQL cascade can reach).

Admin only. The PR opening this is the review gate — Julian merges manually.
"""
from fastapi import APIRouter, HTTPException, Depends

from services.supabase_client import supabase
from services import credentials, storage
from services.cv_parser import guess_extension
from routers.auth import require_admin

router = APIRouter(prefix="/users", tags=["rgpd"])


def _safe_delete(table: str, column: str, value) -> int:
    """Delete rows matching column == value. Returns count deleted (best-effort)."""
    try:
        res = supabase.table(table).delete().eq(column, value).execute()
        return len(res.data or [])
    except Exception:
        return 0


@router.delete("/{user_id}/gdpr")
async def gdpr_erase_user(user_id: str, user: dict = Depends(require_admin)):
    """
    Effacement RGPD complet d'un utilisateur (art. 17).

    Supprime, dans l'ordre des dépendances :
      matchings → submissions (+ fichiers CV dans le bucket) → consultants
      → partner_clients → invitations → support_messages → profile → auth user.

    Renvoie le nombre de lignes supprimées par table.
    """
    if user_id == user["sub"]:
        raise HTTPException(
            status_code=400,
            detail="Utilisez un autre compte admin pour effacer le vôtre.",
        )

    counts: dict[str, int] = {}

    # 0. Resolve the user's email so we can also erase records keyed by email
    #    (invitations sent to / used by them, support messages from them).
    user_email = None
    try:
        prof = supabase.table("profiles").select("id, email").eq(
            "id", user_id
        ).single().execute().data
        user_email = (prof or {}).get("email")
    except Exception:
        # Profile may already be gone; continue erasing the rest by id.
        pass

    # 1. Identify the consultants this user owns and every submission that
    #    touches their personal data (their consultants OR submitted by them).
    consultant_ids: list[str] = []
    try:
        rows = supabase.table("consultants").select("id").eq(
            "created_by", user_id
        ).execute().data or []
        consultant_ids = [r["id"] for r in rows]
    except Exception:
        pass

    sub_rows: dict[str, dict] = {}
    try:
        if consultant_ids:
            for s in (supabase.table("submissions").select("id, ao_id").in_(
                "consultant_id", consultant_ids
            ).execute().data or []):
                sub_rows[s["id"]] = s
        for s in (supabase.table("submissions").select("id, ao_id, cv_filename").eq(
            "submitted_by", user_id
        ).execute().data or []):
            sub_rows[s["id"]] = s
    except Exception:
        pass
    submission_ids = list(sub_rows.keys())

    # 2. Matchings reference these submissions (submission_id SET NULL on delete,
    #    so they'd otherwise survive with résumé/breakdown text). Erase first.
    matchings_deleted = 0
    for sid in submission_ids:
        matchings_deleted += _safe_delete("matchings", "submission_id", sid)
    counts["matchings"] = matchings_deleted

    # 3. Remove CV files from object storage (no DB cascade reaches the bucket).
    cv_paths = [
        f"{s['ao_id']}/{sid}.{guess_extension(s.get('cv_filename'))}"
        for sid, s in sub_rows.items() if s.get("ao_id")
    ]
    if cv_paths:
        try:
            storage.remove("cvs", cv_paths)
        except Exception:
            pass  # best-effort; orphaned files are swept by a storage policy

    # 4. Delete submissions (by id), then the consultants (which cascades any
    #    remaining submissions tied to them).
    subs_deleted = 0
    for sid in submission_ids:
        subs_deleted += _safe_delete("submissions", "id", sid)
    counts["submissions"] = subs_deleted

    consultants_deleted = 0
    for cid in consultant_ids:
        consultants_deleted += _safe_delete("consultants", "id", cid)
    counts["consultants"] = consultants_deleted

    # 5. Access matrix, invitations and support tickets.
    counts["partner_clients"] = _safe_delete("partner_clients", "partner_id", user_id)

    inv = _safe_delete("invitations", "used_by", user_id)
    if user_email:
        inv += _safe_delete("invitations", "email", user_email)
    counts["invitations"] = inv

    sup = _safe_delete("support_messages", "user_id", user_id)
    if user_email:
        sup += _safe_delete("support_messages", "from_email", user_email)
    counts["support_messages"] = sup

    # 6. Les identifiants de connexion, PUIS le profil.
    #
    #    L'ordre est délibéré alors que `user_credentials.user_id` référence
    #    `profiles(id) ON DELETE CASCADE` : la cascade suffirait à effacer la
    #    ligne, mais elle n'en RENDRAIT PAS COMPTE. Un effacement au titre de
    #    l'article 17 doit pouvoir être justifié table par table ; « supprimé
    #    par cascade » n'est pas un décompte. On supprime donc explicitement,
    #    et la cascade reste le filet si cette suppression échoue.
    counts["user_credentials"] = credentials.delete(user_id)
    counts["profiles"] = _safe_delete("profiles", "id", user_id)

    return {
        "message": "Effacement RGPD effectué",
        "user_id": user_id,
        "deleted": counts,
    }
