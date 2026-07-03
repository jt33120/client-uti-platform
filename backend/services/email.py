"""
Shared email sending service.

Sends transactional emails through an SMTP server (Infomaniak by default)
using STARTTLS on port 587. All public helpers return a ``(success, error)``
tuple and never raise, so callers can treat email delivery as best-effort.
"""
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from typing import Optional

from config import settings


# ── Shared branded email template ──────────────────────────────────
# Every transactional email is rendered through render_email_html() so they
# all share the same shell: logo, "Groupement-IT" wordmark, card layout and
# footer. Callers only provide the inner content (title + body + optional CTA).
BRAND = "Groupement-IT"


def _logo_url() -> str:
    return f"{settings.frontend_url.rstrip('/')}/logo.png"


def render_email_html(
    *,
    title: str,
    body_html: str,
    cta: Optional[dict] = None,
    footer_note: Optional[str] = None,
) -> str:
    """
    Render a branded HTML email.

    - ``title``       : H1 shown under the logo (e.g. "Bonjour Jean,").
    - ``body_html``   : inner HTML of the main block (paragraphs, tables…).
    - ``cta``         : optional ``{"label", "url"}`` → black button + copyable link.
    - ``footer_note`` : optional small grey note in the bottom (bordered) row.
    """
    # Couleurs de marque (bleu plateforme).
    brand = "#4f46e5"
    brand_grad = "linear-gradient(135deg,#6366f1,#4f46e5)"
    band = "#eef2ff"   # indigo très clair (bandeau d'en-tête)

    # Le titre et le CTA peuvent contenir des valeurs issues d'utilisateurs
    # (nom de profil…) : échappés systématiquement — le body_html, lui, est
    # construit par les appelants qui échappent leurs propres champs.
    import html as _html
    title = _html.escape(str(title or ""))
    if footer_note:
        footer_note = _html.escape(str(footer_note))
    if cta:
        cta = {"label": _html.escape(str(cta.get("label") or "")),
               "url": _html.escape(str(cta.get("url") or ""), quote=True)}

    cta_html = ""
    if cta:
        cta_html = f"""
            <tr>
              <td align="center" style="padding:8px 32px 34px;">
                <a href="{cta['url']}"
                   style="display:inline-block;background:{brand};background-image:{brand_grad};color:#ffffff;text-decoration:none;font-weight:600;font-size:15px;padding:14px 32px;border-radius:10px;">
                  {cta['label']}
                </a>
                <p style="font-size:12px;color:#9098a3;margin:18px 0 0;word-break:break-all;">
                  Ou copiez ce lien :<br/>
                  <a href="{cta['url']}" style="color:{brand};text-decoration:none;">{cta['url']}</a>
                </p>
              </td>
            </tr>"""

    footer_html = f"""
            <tr>
              <td style="padding:18px 32px;border-top:1px solid #ececf2;font-size:12px;color:#9098a3;background:#fafafb;">
                {footer_note or f"Cet email vous est envoyé par la plateforme {BRAND}."}
              </td>
            </tr>"""

    return f"""\
<!DOCTYPE html>
<html lang="fr">
  <body style="margin:0;padding:0;background:#f4f4f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#1d1d1f;">
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f7;padding:32px 16px;">
      <tr>
        <td align="center">
          <table width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;background:#ffffff;border:1px solid #e7e7ee;border-radius:14px;overflow:hidden;">
            <!-- Filet d'accent en haut -->
            <tr><td style="height:4px;background:{brand};background-image:{brand_grad};font-size:0;line-height:0;">&nbsp;</td></tr>
            <!-- Bandeau logo + nom -->
            <tr>
              <td style="padding:24px 32px;background:{band};">
                <table cellpadding="0" cellspacing="0"><tr>
                  <td style="vertical-align:middle;padding-right:12px;">
                    <img src="{_logo_url()}" alt="{BRAND}" height="40" style="height:40px;width:auto;display:block;" />
                  </td>
                  <td style="vertical-align:middle;font-size:15px;font-weight:700;letter-spacing:0.06em;color:{brand};text-transform:uppercase;">
                    {BRAND}
                  </td>
                </tr></table>
              </td>
            </tr>
            <!-- Titre -->
            <tr>
              <td style="padding:28px 32px 0;">
                <h1 style="font-size:21px;line-height:1.3;margin:0;font-weight:700;color:#15171c;">{title}</h1>
              </td>
            </tr>
            <!-- Corps -->
            <tr>
              <td style="padding:16px 32px 22px;font-size:15px;line-height:1.6;color:#3a3f4a;">
                {body_html}
              </td>
            </tr>{cta_html}{footer_html}
          </table>
          <p style="font-size:11px;color:#b0b4bd;margin:18px 0 0;">© {BRAND}</p>
        </td>
      </tr>
    </table>
  </body>
</html>"""



def send_email(
    to_email: str,
    subject: str,
    html: str,
    text: Optional[str] = None,
    reply_to: Optional[str] = None,
) -> tuple[bool, Optional[str]]:
    """
    Send a single HTML email via the configured SMTP server.

    Returns ``(success, error_message)``. Never raises — the caller decides
    whether a delivery failure is blocking.
    """
    if not settings.smtp_host:
        return False, "SMTP_HOST non configuré"
    if not settings.smtp_user or not settings.smtp_password:
        return False, "Identifiants SMTP (SMTP_USER / SMTP_PASSWORD) non configurés"

    from_email = settings.smtp_from or settings.smtp_user

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((settings.smtp_from_name, from_email))
    msg["To"] = to_email
    if reply_to:
        msg["Reply-To"] = reply_to

    # Plain-text fallback first, then HTML as the preferred alternative.
    msg.set_content(text or "Cet email nécessite un client compatible HTML.")
    msg.add_alternative(html, subtype="html")

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
            server.starttls(context=context)
            server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
        return True, None
    except smtplib.SMTPException as e:
        return False, f"SMTP: {e}"
    except Exception as e:
        return False, str(e)
