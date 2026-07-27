"""Résolution de l'IP de l'appelant derrière la chaîne Vercel → nginx → uvicorn.

Deux notions à ne surtout pas confondre :

* **IP de confiance** — ``X-Real-IP``, posée par NOTRE nginx (``$remote_addr``),
  donc non falsifiable de l'extérieur. Mais comme le front passe par la
  réécriture ``/api/*`` de Vercel, elle vaut l'IP de **sortie de Vercel**
  (ex. ``15.237.x.x``), la même pour tous les utilisateurs. Elle sert à
  l'anti-abus / throttling — voir ``routers.auth._client_ip`` et
  ``services.ratelimit._client_ip``, inchangés.

* **IP publique de l'utilisateur** — ``public_client_ip`` ci-dessous, pour
  l'affichage et l'audit (« dernière connexion » : modale profil + page Comptes
  de l'admin). Vercel pose l'IP réelle du navigateur en tête de
  ``X-Forwarded-For`` **avant** que nginx n'y ajoute son propre pair ; c'est
  donc la valeur la plus à gauche qu'il faut lire.

  Cette valeur est DÉCLARATIVE (forgeable par qui taperait le VPS en direct,
  hors Vercel) : elle est faite pour être affichée, jamais pour décider d'un
  accès ou d'un blocage.

Module volontairement sans dépendance (pas d'import FastAPI) : la requête est
seulement attendue avec un ``.headers`` façon mapping et un ``.client.host``.
"""
from __future__ import annotations

import ipaddress
from typing import Any, Optional


def public_client_ip(request: Optional[Any]) -> Optional[str]:
    """IP publique réelle de l'utilisateur, pour l'affichage/audit.

    Parcourt les candidats du plus proche de l'utilisateur au plus proche de
    nous (``X-Forwarded-For`` de gauche à droite, puis ``X-Real-IP``, puis le
    pair TCP) et retient la première adresse **publique** valide. Les entrées
    malformées ou privées (en-tête bricolé, réseau interne) sont ignorées :
    afficher ``192.168.1.10`` comme « IP de connexion » n'aurait aucun sens.

    Renvoie ``None`` si la requête est absente, sinon au pire l'IP de confiance
    quand aucune adresse publique n'est exploitable (dev local).
    """
    if request is None:
        return None

    headers = getattr(request, "headers", None) or {}
    trusted = (headers.get("x-real-ip") or "").strip() or None
    client = getattr(request, "client", None)
    peer = getattr(client, "host", None) if client else None

    candidates: list[str] = []
    for part in (headers.get("x-forwarded-for") or "").split(","):
        part = part.strip()
        if part:
            candidates.append(part)
    if trusted:
        candidates.append(trusted)
    if peer:
        candidates.append(peer)

    for candidate in candidates:
        try:
            if ipaddress.ip_address(candidate).is_global:
                return candidate
        except ValueError:
            continue  # entrée malformée → on l'ignore au lieu de l'afficher

    # Aucune IP publique exploitable (dev local, réseau privé) : mieux vaut
    # l'IP de confiance que rien du tout.
    return trusted or peer
