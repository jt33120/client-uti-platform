"""
Templates d'emails éditables (menu Administration → Templates Mails).

Le sujet et le corps de chaque email transactionnel sont éditables par l'admin
via un éditeur visuel riche (gras, couleurs, images, boutons, listes…) et
stockés en base (`email_templates`). À défaut de ligne stockée, on retombe sur
les valeurs par défaut ci-dessous : le code reste fonctionnel même sans la
migration.

Le corps est stocké en **HTML** (format "html"). Pour la rétro-compatibilité,
un corps en texte brut (format "text", anciennes lignes) reste rendu comme
avant. Le sujet est toujours du texte simple.

Chaque template déclare ses propres variables (`placeholders`). À l'envoi, on
remplace toutes les `{clé}` présentes dans le contexte fourni.
"""
import re
import html as _html
from datetime import date, datetime
from services.supabase_client import supabase


def format_date_fr(value) -> str:
    """Convertit une date ISO (YYYY-MM-DD ou datetime ISO) en JJ/MM/AAAA.

    Renvoie la valeur d'origine inchangée si elle n'est pas parsable (on
    n'altère jamais un texte libre qui ne serait pas une date).
    """
    if not value:
        return value
    if isinstance(value, (date, datetime)):
        return value.strftime("%d/%m/%Y")
    head = str(value).strip()[:10]  # partie date d'un éventuel datetime ISO
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", head)
    if m:
        y, mo, d = m.groups()
        return f"{d}/{mo}/{y}"
    return value

# Variables mises en évidence (gras) dans le rendu des anciens corps "texte".
_BOLD = {"title", "client"}

# ── Corps par défaut — HTML riche, styles inline (compatibles clients mail) ──
# Le titre de l'AO est présenté UNE fois, comme bloc mis en valeur (pas dans le
# H1, qui reste un libellé court) pour éviter les pavés en double.
_AO_TITLE_BLOCK = (
    '<p style="margin:0 0 16px;padding:12px 16px;background:#f4f5fb;'
    'border-left:3px solid #4f46e5;border-radius:6px;font-size:16px;'
    'font-weight:700;color:#1d1d1f;">{title}</p>'
)
# `{greeting}` porte l'accroche nominative (« Bonjour Marc, » ou « Bonjour, » à
# défaut de nom connu). Le rendu étant désormais fait PAR DESTINATAIRE, la même
# campagne produit un corps différent pour chacun.
_DEFAULT_NEW = (
    '<p style="margin:0 0 12px;">{greeting}</p>'
    '<p style="margin:0 0 4px;">Un nouvel appel d\'offres vient d\'être ouvert '
    "pour le client <strong>{client}</strong> :</p>"
    + _AO_TITLE_BLOCK +
    '<p style="margin:0;">Vous pouvez dès à présent proposer un consultant '
    "correspondant directement sur la plateforme.</p>"
)
_DEFAULT_RELANCE = (
    '<p style="margin:0 0 12px;">{greeting}</p>'
    '<p style="margin:0 0 4px;">Pour rappel, cet appel d\'offres pour '
    "<strong>{client}</strong> est toujours ouvert :</p>"
    + _AO_TITLE_BLOCK +
    '<p style="margin:0;">Nous n\'avons pas encore reçu de proposition de votre '
    "part — n'hésitez pas à nous proposer un profil avant la date limite.</p>"
)
_DEFAULT_INVITE = (
    '<p style="margin:0 0 14px;">Vous êtes invité(e) à rejoindre '
    "<strong>{role}</strong>.</p>"
    '<p style="margin:0 0 14px;">Créez votre compte en cliquant sur le bouton '
    "ci-dessous.</p>"
    '<p style="margin:0;font-size:13px;color:#6e6e73;">Ce lien est à usage '
    "unique et expire dans 7 jours.</p>"
)
_DEFAULT_RESET = (
    '<p style="margin:0 0 14px;">Vous avez demandé à réinitialiser le mot de '
    "passe de votre compte sur la plateforme Groupement-IT.</p>"
    '<p style="margin:0 0 14px;">Cliquez sur le bouton ci-dessous pour choisir '
    "un nouveau mot de passe.</p>"
    '<p style="margin:0;font-size:13px;color:#6e6e73;">Ce lien est à usage '
    "unique et expire dans 1 heure.</p>"
)
# Migration de l'authentification. VOLONTAIREMENT DISTINCT de _DEFAULT_RESET :
# celui-ci s'ouvre sur « vous avez demandé », ce qui serait faux ici — personne
# n'a rien demandé. Un utilisateur qui reçoit un lien de mot de passe qu'il n'a
# pas sollicité pense d'abord à une tentative d'hameçonnage, et il a raison de le
# penser. L'email doit donc dire POURQUOI il arrive, et ce qui NE change pas.
_DEFAULT_MIGRATION = (
    # PREMIÈRE PHRASE = POURQUOI CET E-MAIL ARRIVE MAINTENANT.
    # Un destinataire qui n'a rien demandé cherche d'abord à comprendre ce qui
    # lui vaut ce message. Tant qu'il ne le sait pas, tout le reste se lit comme
    # un prétexte — et « Action requise » suivi d'un lien de mot de passe est le
    # gabarit exact d'un hameçonnage.
    '<p style="margin:0 0 14px;">Vous recevez cet e-mail parce que la plateforme '
    "Groupement-IT a changé de serveur. Les mots de passe n'ont pas été conservés "
    "lors de l'opération : il vous faut en choisir un nouveau, une seule fois. "
    "<strong>Vous n'avez rien demandé, et c'est normal.</strong></p>"
    '<p style="margin:0 0 14px;"><strong>Votre compte et vos données sont '
    "intacts</strong> — appels d'offres, consultants, historique. Seule la façon "
    "de vous connecter change.</p>"
    '<p style="margin:0 0 14px;">Si la double authentification était déjà active '
    "sur votre compte, votre application d'authentification continue de "
    "fonctionner : aucun nouveau QR code à scanner.</p>"
    '<p style="margin:0 0 14px;font-size:13px;color:#6e6e73;">Ce lien est à usage '
    "unique et expire dans {validite}.</p>"
    # LE MOYEN DE VÉRIFIER SANS NOUS CROIRE.
    # Demander « faites-nous confiance » dans un e-mail est sans valeur : un
    # hameçonneur écrirait la même phrase. On donne donc un chemin qui ne passe
    # PAS par ce message — ouvrir soi-même la plateforme et y demander le lien
    # aboutit exactement au même endroit. Le prudent a une issue, et le réflexe
    # qu'on lui apprend est le bon partout ailleurs.
    '<div style="margin:0;padding:12px 14px;border-radius:8px;background:#f5f5f7;'
    'font-size:13px;color:#3a3f4a;">'
    "<strong>Un doute sur cet e-mail ?</strong> Ne cliquez pas. Ouvrez vous-même "
    '<a href="{plateforme}/login" style="color:#3a3f4a;">{plateforme}</a> dans '
    "votre navigateur et utilisez « Mot de passe oublié » : vous obtiendrez "
    "exactement le même lien. Une question, ou vous pensez que cet e-mail n'aurait "
    'pas dû vous parvenir ? Écrivez-nous depuis la <a href="{plateforme}/contact" '
    'style="color:#3a3f4a;">page Contact</a> de la plateforme.'
    "</div>"
)

# `placeholders` : variables proposées dans l'éditeur.
# `email_title`  : H1 court de l'email (≠ corps), peut utiliser des variables.
# `cta_label`    : libellé du bouton d'action (le lien = {link} du contexte).
# `footer`       : note de pied de page.
DEFAULTS = {
    "ao_new": {
        "label": "Nouvel appel d'offres — notification aux partenaires",
        "subject": "Nouvel appel d'offres : {title}",
        "body": _DEFAULT_NEW,
        "format": "html",
        "placeholders": ["greeting", "partner_name", "title", "client",
                         "reference", "location", "deadline", "link"],
        "email_title": "Nouvel appel d'offres",
        "cta_label": "Voir l'appel d'offres",
        "footer": "Vous recevez cet email car vous êtes partenaire référencé sur ce client.",
    },
    "ao_relance": {
        "label": "Relance des partenaires — AO resté sans réponse",
        "subject": "Rappel, appel d'offres : {title}",
        "body": _DEFAULT_RELANCE,
        "format": "html",
        "placeholders": ["greeting", "partner_name", "title", "client",
                         "reference", "location", "deadline", "link"],
        "email_title": "Appel d'offres toujours ouvert",
        "cta_label": "Proposer un consultant",
        "footer": "Vous recevez cet email car vous êtes partenaire référencé sur ce client.",
    },
    "invite": {
        "label": "Invitation — création de compte (partenaire / commercial)",
        "subject": "Invitation à la plateforme Groupement-IT",
        "body": _DEFAULT_INVITE,
        "format": "html",
        "placeholders": ["name", "role", "link"],
        "email_title": "Bonjour {name},",
        "cta_label": "Créer mon compte",
        "footer": "Si vous n'attendiez pas cette invitation, ignorez simplement cet email.",
    },
    "password_reset": {
        "label": "Mot de passe oublié — lien de réinitialisation",
        "subject": "Réinitialisation de votre mot de passe",
        "body": _DEFAULT_RESET,
        "format": "html",
        "placeholders": ["link"],
        "email_title": "Réinitialisation du mot de passe",
        "cta_label": "Réinitialiser mon mot de passe",
        "footer": "Si vous n'êtes pas à l'origine de cette demande, ignorez cet email, votre mot de passe reste inchangé.",
    },
    "password_migration": {
        "label": "Migration de l'authentification — définir son mot de passe",
        "subject": "Action requise : définissez votre mot de passe Groupement-IT",
        "body": _DEFAULT_MIGRATION,
        "format": "html",
        "placeholders": ["name", "link", "validite", "plateforme"],
        "email_title": "Bonjour {name},",
        "cta_label": "Définir mon mot de passe",
        # Le pied de page d'un e-mail de réinitialisation dit « ignorez, votre
        # mot de passe reste inchangé ». Ici ce serait un contresens : ignorer
        # l'e-mail, c'est rester dehors. On dit donc l'inverse, et on donne un
        # recours — sans quoi le seul réflexe possible est d'écrire à un
        # expéditeur qu'on soupçonne déjà d'être un imposteur.
        "footer": "Sans ce mot de passe, vous ne pourrez plus vous connecter. Si le lien a expiré, utilisez « Mot de passe oublié » sur la page de connexion.",
    },
    # ── Annonce / communication aux partenaires (demande Sullyvan) ────────────
    "annonce_pilote": {
        "label": "Annonce — lancement plateforme / phase pilote (→ partenaires)",
        "subject": "Nouvelle plateforme de gestion des appels d'offres — phase pilote GIT",
        "body": (
            '<p style="margin:0 0 14px;">Bonjour chers partenaires,</p>'
            '<p style="margin:0 0 14px;">Afin de gagner en réactivité et de poursuivre '
            "notre croissance, nous avons développé une nouvelle plateforme de gestion de "
            "nos appels d'offres clients GIT, et souhaitons lancer ces prochains jours une "
            "<strong>phase pilote</strong> avec l'un de nos clients : <strong>CMA CGM</strong>.</p>"
            '<p style="margin:0 0 14px;">Ce pilote se fera en lien avec quelques partenaires '
            "sélectionnés — dont vous avez l'honneur de faire partie 😊 !</p>"
            '<p style="margin:0 0 14px;">La plateforme sera bientôt accessible à l\'adresse : '
            '<a href="https://plateforme.groupement-it.com/" style="color:#4f46e5;">'
            "https://plateforme.groupement-it.com/</a></p>"
            '<p style="margin:0 0 8px;">En production, elle vous permettra, pour tous nos '
            "clients GIT, de :</p>"
            '<ul style="margin:0 0 14px;padding-left:20px;color:#1d1d1f;">'
            '<li style="margin:0 0 6px;">consulter les appels d\'offres en cours ;</li>'
            '<li style="margin:0 0 6px;">télécharger les dossiers de consultation ;</li>'
            '<li style="margin:0;">suivre l\'avancement de vos candidatures.</li>'
            "</ul>"
            '<p style="margin:0 0 14px;">Pour entrer dans cette première phase de test, nous '
            "vous adresserons prochainement un e-mail contenant vos <strong>identifiants de "
            "connexion</strong>.</p>"
            '<p style="margin:0 0 14px;">Nous comptons sur vous pour nous faire vos retours '
            "précis, tant sur le fond que sur la forme, ainsi que sur les avantages que vous "
            "y percevez.</p>"
            '<p style="margin:0;">Vous en remerciant par avance.</p>'
        ),
        "format": "html",
        "placeholders": ["link"],
        "email_title": "Nouvelle plateforme partenaires — phase pilote",
        "cta_label": "Découvrir la plateforme",
        "footer": "Vous recevez cet email en tant que partenaire du Groupement-IT "
                  "sélectionné pour la phase pilote.",
        # Annonce → diffusable en masse aux partenaires d'un client.
        "broadcast": True,
    },
    # ── Notifications « Validation CV » (demande Sullyvan) ────────────────────
    "cv_retenu": {
        "label": "Validation CV — CV retenu par GRP-IT (→ partenaire)",
        "subject": "Profil retenu : {consultant} — {title}",
        "body": "<p>Bonne nouvelle : le profil <strong>{consultant}</strong> que vous avez proposé a été "
                "<strong>retenu</strong> par le Groupement-IT pour l'appel d'offres « {title} » ({client}).</p>"
                "<p>Nous revenons vers vous pour la suite.</p>",
        "format": "html",
        "placeholders": ["consultant", "title", "client", "reference", "link"],
        "email_title": "Profil retenu",
        "cta_label": "Voir l'appel d'offres",
        "footer": "Vous recevez cet email en tant que partenaire porteur de ce profil.",
    },
    "cv_non_retenu": {
        "label": "Validation CV — CV non retenu par GRP-IT (→ partenaire)",
        "subject": "Profil non retenu — {title}",
        "body": "<p>Le profil <strong>{consultant}</strong> proposé pour l'appel d'offres « {title} » ({client}) "
                "n'a pas été retenu par le Groupement-IT.</p>"
                "<p>Merci pour votre proposition — n'hésitez pas à nous soumettre d'autres profils.</p>",
        "format": "html",
        "placeholders": ["consultant", "title", "client", "reference", "link"],
        "email_title": "Profil non retenu",
        "cta_label": "Voir l'appel d'offres",
        "footer": "Vous recevez cet email en tant que partenaire porteur de ce profil.",
    },
    "cv_envoye_client": {
        "label": "Validation CV — CV envoyé au client (→ partenaire)",
        "subject": "Profil transmis au client — {title}",
        "body": "<p>Le CV de <strong>{consultant}</strong> a été <strong>transmis au client</strong> "
                "pour l'appel d'offres « {title} » ({client}).</p>"
                "<p>Nous vous tiendrons informé des suites.</p>",
        "format": "html",
        "placeholders": ["consultant", "title", "client", "reference", "link"],
        "email_title": "Profil transmis au client",
        "cta_label": "Voir l'appel d'offres",
        "footer": "Vous recevez cet email en tant que partenaire porteur de ce profil.",
    },
    "echange_commercial": {
        "label": "Validation CV — Échange commercial en cours (→ partenaire)",
        "subject": "Échange commercial en cours — {title}",
        "body": "<p>Un <strong>échange commercial</strong> est en cours concernant le profil "
                "<strong>{consultant}</strong> pour l'appel d'offres « {title} » ({client}).</p>",
        "format": "html",
        "placeholders": ["consultant", "title", "client", "reference", "link"],
        "email_title": "Échange commercial",
        "cta_label": "Voir l'appel d'offres",
        "footer": "Vous recevez cet email en tant que partenaire porteur de ce profil.",
    },
    "affaire_gagnee": {
        "label": "Validation CV — Affaire gagnée (→ partenaire)",
        "subject": "Affaire gagnée — {title}",
        "body": "<p>🎉 L'affaire « {title} » ({client}) est <strong>gagnée</strong> avec le profil "
                "<strong>{consultant}</strong>. Félicitations et merci pour votre collaboration !</p>",
        "format": "html",
        "placeholders": ["consultant", "title", "client", "reference", "link"],
        "email_title": "Affaire gagnée",
        "cta_label": "Voir l'appel d'offres",
        "footer": "Vous recevez cet email en tant que partenaire porteur de ce profil.",
    },
    "affaire_perdue": {
        "label": "Validation CV — Affaire perdue (→ partenaire)",
        "subject": "Affaire non aboutie — {title}",
        "body": "<p>L'affaire « {title} » ({client}) proposée avec le profil <strong>{consultant}</strong> "
                "n'a malheureusement pas abouti. Merci pour votre implication.</p>",
        "format": "html",
        "placeholders": ["consultant", "title", "client", "reference", "link"],
        "email_title": "Affaire non aboutie",
        "cta_label": "Voir l'appel d'offres",
        "footer": "Vous recevez cet email en tant que partenaire porteur de ce profil.",
    },
    "cv_client": {
        "label": "Validation CV — Envoi du CV au client (→ client)",
        "subject": "Proposition de profil — {title}",
        "body": "<p>Bonjour,</p>"
                "<p>Dans le cadre de « {title} », veuillez trouver ci-dessous le CV du profil que nous vous proposons.</p>"
                "<p>{message}</p>",
        "format": "html",
        "placeholders": ["title", "reference", "message", "link"],
        "email_title": "Proposition de profil",
        "cta_label": "Consulter le CV",
        "footer": "Email envoyé par le Groupement-IT.",
    },
}


def _stored() -> dict:
    """Lignes stockées indexées par clé (best-effort : {} si table absente)."""
    try:
        rows = supabase.table("email_templates").select("*").execute().data or []
        return {r["key"]: r for r in rows}
    except Exception:
        return {}


def _looks_html(s: str) -> bool:
    return bool(re.search(r"<[a-zA-Z!/][^>]*>", s or ""))


def _strip_html(s: str) -> str:
    """Réduit un corps HTML en texte brut lisible (fallback texte de l'email)."""
    s = s or ""
    s = re.sub(r"(?is)<\s*(br)\s*/?>", "\n", s)
    s = re.sub(r"(?is)<\s*li[^>]*>", "• ", s)
    s = re.sub(r"(?is)</\s*(p|div|li|h[1-6]|tr|ul|ol)\s*>", "\n", s)
    s = re.sub(r"(?is)<[^>]+>", "", s)
    s = _html.unescape(s)
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def get_all() -> list[dict]:
    """Tous les templates (stocké fusionné sur le défaut), pour l'UI admin."""
    stored = _stored()
    out = []
    for key, d in DEFAULTS.items():
        row = stored.get(key) or {}
        out.append({
            "key": key,
            "label": d["label"],
            "subject": row.get("subject") or d["subject"],
            "body": row.get("body") or d["body"],
            "format": row.get("format") or d.get("format", "html"),
            "default_subject": d["subject"],
            "default_body": d["body"],
            "default_format": d.get("format", "html"),
            "is_custom": bool(row),
            "placeholders": d.get("placeholders", []),
            # Diffusable en masse aux partenaires (annonces) : les modèles
            # transactionnels liés à un AO précis ne le sont pas.
            "broadcast": bool(d.get("broadcast")),
        })
    return out


def _effective(key: str) -> dict:
    """Template effectif pour un envoi (stocké sinon défaut)."""
    d = DEFAULTS.get(key, {"subject": "{title}", "body": "", "format": "html"})
    row = _stored().get(key) or {}
    return {
        "subject": row.get("subject") or d["subject"],
        "body": row.get("body") or d["body"],
        "format": row.get("format") or d.get("format", "html"),
    }


def raw_body(key: str) -> str:
    """Corps brut effectif (non rendu) — sert au dédoublonnage du bloc méta."""
    return _effective(key)["body"]


def render_subject(key: str, context: dict) -> str:
    s = _effective(key)["subject"]
    for name, val in context.items():
        s = s.replace("{" + name + "}", str(val or ""))
    return s.strip() or "Groupement-IT"


def _inject_html(body: str, context: dict) -> str:
    """Injecte les variables (valeurs échappées) dans un corps HTML déjà sûr."""
    out = body
    for name, val in context.items():
        out = out.replace("{" + name + "}", _html.escape(str(val or "")))
    return out


def render_body(key: str, context: dict, as_html: bool) -> str:
    """Corps rendu.

    - HTML + format html : corps de confiance (éditeur admin), on injecte
      seulement les variables (valeurs échappées).
    - HTML + format texte (legacy) : on échappe, met {title}/{client} en gras,
      et convertit les retours ligne.
    - Texte : on retire le HTML éventuel et on injecte les valeurs brutes.
    """
    eff = _effective(key)
    body, fmt = eff["body"], eff["format"]
    is_html = fmt == "html" or _looks_html(body)

    if not as_html:
        text = _strip_html(body) if is_html else body
        for name, val in context.items():
            text = text.replace("{" + name + "}", str(val or ""))
        return text

    if is_html:
        return _inject_html(body, context)

    # Legacy : corps texte brut → échappé, variables en gras, sauts de ligne.
    out = _html.escape(body)
    for name, val in context.items():
        v = _html.escape(str(val or ""))
        rep = f"<strong>{v}</strong>" if (name in _BOLD and v) else v
        out = out.replace("{" + name + "}", rep)
    return out.replace("\n", "<br>")


def _subst(s: str, context: dict) -> str:
    for name, val in context.items():
        s = s.replace("{" + name + "}", str(val or ""))
    return s


def _meta_table(context: dict) -> str:
    """Tableau Référence / Localisation / Date limite (emails AO)."""
    rows = ""
    for label, key in (("Référence", "reference"), ("Localisation", "location"), ("Date limite", "deadline")):
        val = context.get(key)
        if val:
            rows += (
                f'<tr><td style="padding:4px 0;color:#9098a3;width:120px;">{_html.escape(label)}</td>'
                f'<td style="color:#3a3f4a;">{_html.escape(str(val))}</td></tr>'
            )
    if not rows:
        return ""
    return f'<table cellpadding="0" cellspacing="0" style="width:100%;font-size:14px;margin-top:14px;">{rows}</table>'


def build_email(key: str, context: dict, subject: str = None, body: str = None) -> tuple[str, str, str]:
    """Construit (sujet, html, texte) d'un email transactionnel — SOURCE UNIQUE.

    Utilisée pour l'envoi réel ET pour l'aperçu, ce qui garantit que l'aperçu
    affiché à l'admin est exactement le mail reçu. `subject`/`body` permettent
    de prévisualiser un contenu non encore enregistré.
    """
    # Import local pour éviter toute dépendance circulaire au chargement.
    from services.email import render_email_html

    # Normalise les dates au format français (JJ/MM/AAAA) — copie pour ne pas
    # muter le contexte de l'appelant. Couvre le tableau méta ET les {deadline}.
    context = dict(context)
    if context.get("deadline"):
        context["deadline"] = format_date_fr(context["deadline"])

    d = DEFAULTS.get(key, {})

    # Sujet
    subj = subject if subject is not None else _effective(key)["subject"]
    subj = _subst(subj, context).strip() or "Groupement-IT"

    # Corps (HTML) — override éventuel (aperçu) ou template effectif.
    if body is not None:
        intro = _inject_html(body, context) if _looks_html(body) else _inject_html(
            "<p>" + _html.escape(body).replace("\n", "<br>") + "</p>", context
        )
        raw = body
    else:
        intro = render_body(key, context, as_html=True)
        raw = raw_body(key)

    # Bloc méta automatique (AO) si le template n'y fait pas déjà référence.
    if key in ("ao_new", "ao_relance"):
        if not any(("{" + v + "}") in (raw or "") for v in ("reference", "location", "deadline")):
            intro += _meta_table(context)

    # H1 court (jamais le titre brut de l'AO).
    title = _subst(d.get("email_title", "Groupement-IT"), context)
    title = title.replace(" ,", ",").strip() or "Groupement-IT"

    # Bouton d'action : lien réel = {link} du contexte.
    cta = None
    link = context.get("link")
    if d.get("cta_label") and link:
        cta = {"label": d["cta_label"], "url": str(link)}

    html = render_email_html(
        title=title, body_html=intro, cta=cta, footer_note=d.get("footer"),
    )

    # Version texte (fallback).
    text_body = render_body(key, context, as_html=False) if body is None else _subst(
        _strip_html(body) if _looks_html(body) else body, context
    )
    text_lines = [title, "", text_body]
    if link:
        text_lines += ["", str(link)]
    return subj, html, "\n".join(text_lines)
