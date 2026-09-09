"""
Désabonnement aux notifications par email.

POURQUOI CE ROUTEUR EXISTE, ET POURQUOI IL EST PUBLIC
Le lien « Ne plus recevoir ce type de notification » est cliqué DEPUIS UNE BOÎTE
MAIL, par quelqu'un qui n'est pas connecté — et souvent par quelqu'un qui ne
veut justement plus rien avoir à faire avec la plateforme. Exiger une connexion
pour se désabonner, c'est garantir que le geste suivant sera « Spam », qui coûte
au domaine d'envoi entier et pas seulement à cet email.

Le modèle est donc celui d'une CAPACITÉ, comme /files/d : l'adresse ET la
catégorie sont À L'INTÉRIEUR du jeton signé (services/email_optout.py), il
n'existe aucun paramètre hors signature, et détenir le lien vaut autorisation —
pour la seule action de cesser d'envoyer des emails à cette adresse-là.

DEUX ENTRÉES, PARCE QU'IL Y A DEUX FAÇONS DE CLIQUER
  GET  /emails/unsubscribe?token=…   le lien du pied de page, ouvert par un
                                     humain dans son navigateur → page de
                                     confirmation lisible.
  POST /emails/unsubscribe?token=…   le bouton « Se désabonner » natif de Gmail
                                     et d'Outlook (RFC 8058, en-tête
                                     List-Unsubscribe-Post posé par
                                     services/email.build_message) → pas de
                                     page, juste un 200.
"""
from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse

from services import email_optout
from services.ratelimit import rate_limit_public

router = APIRouter(prefix="/emails", tags=["emails"])

#: Le jeton ne dit rien de l'IP qui le présente : sans plafond, une adresse
#: fuitée permettrait de marteler l'insertion. 30 appels par heure et par IP
#: laissent largement passer un humain qui reclique, et rien d'autre.
_LIMITE = rate_limit_public(30, 3600)


def _page(titre: str, message: str, ok: bool) -> HTMLResponse:
    """Page de confirmation autoportante.

    Pas de redirection vers le frontend : il est déployé séparément (Vercel), et
    un désabonnement qui dépend d'un second service pour AFFICHER son résultat
    échoue chaque fois que ce service bouge. La page tient donc en un fichier,
    sans CSS externe ni JavaScript — comme le mail qui l'a amenée.
    """
    accent = "#4f46e5" if ok else "#b42318"
    return HTMLResponse(
        status_code=200 if ok else 400,
        content=f"""<!DOCTYPE html>
<html lang="fr">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta name="robots" content="noindex" />
    <title>{titre}</title>
  </head>
  <body style="margin:0;padding:48px 16px;background:#f4f4f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#1d1d1f;">
    <table width="100%" cellpadding="0" cellspacing="0">
      <tr><td align="center">
        <table width="520" cellpadding="0" cellspacing="0" style="max-width:520px;width:100%;background:#fff;border:1px solid #e7e7ee;border-radius:14px;overflow:hidden;">
          <tr><td style="height:4px;background:{accent};font-size:0;line-height:0;">&nbsp;</td></tr>
          <tr><td style="padding:32px;">
            <h1 style="font-size:20px;line-height:1.3;margin:0 0 12px;color:#15171c;">{titre}</h1>
            <p style="font-size:15px;line-height:1.6;margin:0;color:#3a3f4a;">{message}</p>
          </td></tr>
        </table>
        <p style="font-size:11px;color:#b0b4bd;margin:18px 0 0;">Groupement-IT</p>
      </td></tr>
    </table>
  </body>
</html>""",
    )


def _desabonner(token: str, source: str) -> tuple[bool, str, str]:
    """(succès, titre, message) — logique commune au clic humain et au bouton natif."""
    try:
        email, categorie = email_optout.verify(token)
    except ValueError:
        return (False, "Lien invalide",
                "Ce lien de désabonnement n'est pas valide. Répondez à cet email "
                "pour être retiré de la liste.")
    libelle = email_optout.LABELS.get(categorie, categorie)
    if not email_optout.record(email, categorie, source=source):
        # L'écriture a échoué : le dire. Afficher « c'est fait » garantirait que
        # la personne ne réessaie pas et continue de recevoir les emails.
        return (False, "Désabonnement non enregistré",
                "Une erreur est survenue. Réessayez dans quelques minutes, ou "
                "répondez à cet email pour être retiré de la liste.")
    return (True, "Désabonnement enregistré",
            f"<strong>{email}</strong> ne recevra plus les notifications "
            f"« {libelle} ». Les autres emails de la plateforme, dont ceux liés "
            "à votre compte, continuent de vous être envoyés.")


@router.get("/unsubscribe", response_class=HTMLResponse,
            dependencies=[Depends(_LIMITE)])
async def unsubscribe(token: str = Query(..., min_length=16, max_length=2048)):
    """Lien du pied de page, ouvert dans un navigateur."""
    ok, titre, message = _desabonner(token, source="lien")
    return _page(titre, message, ok)


@router.post("/unsubscribe", dependencies=[Depends(_LIMITE)])
async def unsubscribe_one_click(token: str = Query(..., min_length=16, max_length=2048)):
    """Bouton natif du client mail (RFC 8058).

    Le corps envoyé est `List-Unsubscribe=One-Click` ; on ne le lit pas — la
    seule chose qui autorise l'action est le jeton signé, et un corps différent
    ne changerait pas ce qu'il faut faire. La réponse est un 200 sans page :
    aucun humain ne la regarde.
    """
    ok, titre, _ = _desabonner(token, source="entete")
    return {"ok": ok, "status": titre}
