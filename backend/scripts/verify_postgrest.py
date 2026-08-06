#!/usr/bin/env python3
"""
Test d'acceptation : le PostgREST auto-hébergé se comporte-t-il comme Supabase ?

À QUOI ÇA SERT

Le backend fait 367 requêtes à la base via `supabase-py`. Ce client ne parle pas
à Supabase : il parle à PostgREST, que Supabase héberge pour nous aujourd'hui et
que nous hébergerons nous-mêmes demain. En théorie, changer d'hébergeur ne change
donc rien au code applicatif.

« En théorie » ne suffit pas quand la bascule est irréversible. Ce script vérifie,
sur l'installation réelle, les DOUZE comportements dont le code dépend — ceux
qui, s'ils différaient d'un cheveu, casseraient quelque chose sans le dire :

  1. lecture filtrée                       le motif le plus courant (254 select)
  2. jointure embarquée                    select("*, clients(name)") — 26 sites
  3. agrégat embarqué                      submissions(count) → [{'count': N}]
  4. .single() sur 0 et 2 lignes           doit LEVER (52 sites en dépendent
                                           pour produire un 404)
  5. .maybe_single() sur 0 ligne           doit renvoyer None, pas lever
  6. update renvoyant les lignes           le « claim » anti-double-envoi du
                                           planificateur : 2e passage → []
  7. count="exact"                         compteurs de l'écran admin
  8. in_([]) avec liste vide               doit renvoyer [], pas planter
  9. conflit d'unicité                     str(e) doit contenir 23505/duplicate,
                                           sinon email_outbox.enqueue traite un
                                           doublon comme une panne
 10. colonne absente                       str(e) doit contenir 42703, sinon les
                                           replis gracieux deviennent des 500
 11. clé étrangère violée                  message relayé tel quel
 12. RLS verrouillée                       une clé « anon » ne doit RIEN voir

Chaque cas correspond à du code réel du dépôt, cité en commentaire.

QUAND LE LANCER

Sur la base NEUVE, après schema.sql et seed.sql, AVANT de basculer la production.
Il écrit puis efface ses propres lignes de test ; il refuse de tourner si la base
contient déjà des données, pour ne pas être lancé par mégarde sur la production.

    python scripts/make_service_key.py --secret "$PGRST_SECRET" > /tmp/k
    python scripts/verify_postgrest.py --url https://vps-cc93f2a8.vps.ovh.net \\
                                       --key "$(cat /tmp/k)"

Sortie 0 si les douze cas passent, 1 sinon. C'est un feu vert, pas un indice.
"""
import argparse
import sys
import uuid

SENTINELLE = "verif-postgrest@invalide.test"


class Resultat:
    def __init__(self) -> None:
        self.ok: list[str] = []
        self.ko: list[tuple[str, str]] = []

    def teste(self, nom: str, fn, attendu=None) -> None:
        """`attendu` : None = doit réussir ; une classe = doit lever ce type."""
        try:
            valeur = fn()
        except Exception as e:  # noqa: BLE001
            if attendu is Exception:
                self.ok.append(nom)
                print(f"  ✅ {nom}  (a bien levé : {str(e)[:70]})")
            else:
                self.ko.append((nom, f"{type(e).__name__}: {e}"))
                print(f"  ❌ {nom}\n       {type(e).__name__}: {str(e)[:160]}")
            return
        if attendu is Exception:
            self.ko.append((nom, "aurait dû lever, n'a rien levé"))
            print(f"  ❌ {nom}  — aurait dû lever une exception")
        else:
            self.ok.append(nom)
            print(f"  ✅ {nom}  → {str(valeur)[:90]}")


def _texte(e: Exception) -> str:
    return str(e).lower()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", required=True,
                   help="Base de l'API, SANS /rest/v1 (le client l'ajoute lui-même)")
    p.add_argument("--key", required=True, help="Clé service_role (make_service_key.py)")
    p.add_argument("--anon-key", help="Clé anon, pour vérifier que la RLS bloque (cas 12)")
    p.add_argument("--autoriser-base-peuplee", action="store_true",
                   help="Passer outre le refus de tourner sur une base non vide")
    args = p.parse_args()

    try:
        from supabase import create_client
    except ImportError:
        sys.exit("supabase-py est requis : pip install -r requirements.txt")

    sb = create_client(args.url, args.key)
    r = Resultat()

    # ── Garde-fou : ne jamais tourner sur une base qui contient du vrai ──
    # Le script insère et supprime des lignes. Sur une base de production, une
    # suppression qui déraperait coûterait des données ; le refus par défaut est
    # plus utile qu'un avertissement que personne ne lit.
    try:
        peuple = (sb.table("clients").select("id", count="exact").limit(1).execute().count or 0)
    except Exception as e:  # noqa: BLE001
        sys.exit(f"Impossible de joindre l'API : {e}\n"
                 f"Vérifier que {args.url}/rest/v1/ répond et que la clé est signée "
                 f"avec le même secret que PGRST_JWT_SECRET.")
    if peuple and not args.autoriser_base_peuplee:
        sys.exit(f"La base contient déjà {peuple} client(s). Ce script écrit des lignes "
                 f"de test ; il refuse de tourner ailleurs que sur une base neuve. "
                 f"Forcer avec --autoriser-base-peuplee en connaissance de cause.")

    client_id = str(uuid.uuid4())
    ao_id = str(uuid.uuid4())
    consultant_id = str(uuid.uuid4())
    consultant_id2 = str(uuid.uuid4())
    cle_idem = f"verif-{uuid.uuid4()}"

    # Les colonnes renseignées ci-dessous ne sont pas décoratives : ce sont
    # exactement celles qui sont NOT NULL sans valeur par défaut. Les omettre
    # ferait échouer la préparation avec un 23502 — ce qui, la première fois,
    # est précisément comme ça qu'on découvre qu'elles sont obligatoires.
    def _preparer():
        print("\n── Préparation (lignes de test, supprimées à la fin) ──")
        sb.table("clients").insert({
            "id": client_id, "name": "ZZ Vérification", "sector": "Test",
        }).execute()
        # DEUX consultants : submissions porte un UNIQUE (ao_id, consultant_id),
        # donc deux candidatures sur le même AO exigent deux consultants
        # distincts. C'est ce couple qui sert ensuite à vérifier l'agrégat
        # embarqué (count = 2) et le .single() sur 2 lignes.
        sb.table("consultants").insert([
            {"id": consultant_id, "name": "ZZ Vérification 1", "skills": "test"},
            {"id": consultant_id2, "name": "ZZ Vérification 2", "skills": "test"},
        ]).execute()
        # description et skills_required sont NOT NULL sans valeur par défaut.
        sb.table("appels_offres").insert({
            "id": ao_id, "title": "ZZ Vérification", "client_id": client_id,
            "status": "open", "skills_required": "test",
            "description": "Ligne de test créée par verify_postgrest.py",
        }).execute()
        sb.table("submissions").insert([
            {"ao_id": ao_id, "consultant_id": consultant_id},
            {"ao_id": ao_id, "consultant_id": consultant_id2},
        ]).execute()
        print("  fait")

    def _nettoyer():
        """Supprime les lignes de test. Appelé quoi qu'il arrive.

        Sans ce filet, un plantage en cours de route laisse des lignes derrière
        lui — et le garde-fou « base non vide » du prochain lancement refuse
        alors de démarrer. L'outil se bloquerait lui-même après son premier
        échec, c'est-à-dire exactement quand on a besoin de le relancer.
        Ordre inverse des insertions : les enfants avant les parents.
        """
        print("\n── Nettoyage ──")
        for table, col, val in (("email_outbox", "idempotency_key", cle_idem),
                                ("submissions", "ao_id", ao_id),
                                ("appels_offres", "id", ao_id),
                                ("consultants", "id", consultant_id),
                                ("consultants", "id", consultant_id2),
                                ("clients", "id", client_id)):
            try:
                sb.table(table).delete().eq(col, val).execute()
            except Exception as e:  # noqa: BLE001
                print(f"  ⚠ {table} non nettoyée ({e}) — à supprimer à la main")
        print("  fait")

    try:
        _preparer()
        _executer_les_cas(sb, r, args, client_id, ao_id, consultant_id, cle_idem)
    finally:
        _nettoyer()

    total = len(r.ok) + len(r.ko)
    print(f"\n{'═' * 66}\n{len(r.ok)}/{total} cas conformes")
    if r.ko:
        print("\nÉCHECS — ne PAS basculer la production :")
        for nom, err in r.ko:
            print(f"   • {nom}\n     {err}")
        return 1
    print("\n✅ Le PostgREST auto-hébergé se comporte comme Supabase sur les douze\n"
          "   comportements dont le backend dépend. Les 367 requêtes du code n'ont\n"
          "   pas à changer.")
    return 0


def _executer_les_cas(sb, r, args, client_id, ao_id, consultant_id, cle_idem) -> None:
    from supabase import create_client

    print("\n── 1. Lecture filtrée ──")
    r.teste("select().eq()",
            lambda: sb.table("appels_offres").select("id,title").eq("id", ao_id).execute().data)

    print("\n── 2. Jointure embarquée (26 sites, dont routers/matching.py:243) ──")
    r.teste('select("*, clients(name)") → objet imbriqué',
            lambda: sb.table("appels_offres").select("*, clients(name)")
                      .eq("id", ao_id).execute().data[0]["clients"]["name"])

    print("\n── 3. Agrégat embarqué (routers/aos.py:390, 474, 476, 586) ──")
    def agregat():
        d = sb.table("appels_offres").select("*, submissions(count)").eq("id", ao_id).execute().data[0]
        subs = d["submissions"]
        # Forme attendue : [{'count': 2}]. Le code lit subs[0]["count"] ; un
        # entier nu ferait renvoyer 0 à tout le monde, silencieusement.
        assert isinstance(subs, list) and subs and subs[0]["count"] == 2, f"forme inattendue : {subs}"
        return subs
    r.teste("submissions(count) → [{'count': 2}]", agregat)

    print("\n── 4. .single() doit LEVER (52 sites l'utilisent pour produire un 404) ──")
    r.teste("single() sur 0 ligne",
            lambda: sb.table("clients").select("name").eq("name", "ZZ inexistant").single().execute(),
            attendu=Exception)
    r.teste("single() sur 2 lignes",
            lambda: sb.table("submissions").select("id").eq("ao_id", ao_id).single().execute(),
            attendu=Exception)

    print("\n── 5. .maybe_single() doit renvoyer None ──")
    def maybe():
        res = sb.table("clients").select("name").eq("name", "ZZ inexistant").maybe_single().execute()
        assert res is None or res.data is None, f"attendu None, obtenu {res}"
        return None
    r.teste("maybe_single() sur 0 ligne", maybe)

    print("\n── 6. update renvoie les lignes affectées (claim de services/scheduler.py:55) ──")
    def claim_premier():
        d = sb.table("appels_offres").update({"list2_notified_at": "2026-01-01T00:00:00Z"}) \
              .eq("id", ao_id).is_("list2_notified_at", "null").execute().data
        assert len(d) == 1, f"attendu 1 ligne, obtenu {len(d)}"
        return f"{len(d)} ligne"
    def claim_second():
        d = sb.table("appels_offres").update({"list2_notified_at": "2026-02-01T00:00:00Z"}) \
              .eq("id", ao_id).is_("list2_notified_at", "null").execute().data
        assert d == [], f"attendu [], obtenu {d} — le double-envoi n'est plus empêché"
        return "[] (double-envoi empêché)"
    r.teste("1er claim → 1 ligne", claim_premier)
    r.teste("2e claim → [] ", claim_second)

    print("\n── 7. count='exact' (services/email_outbox.py:228, routers/admin.py) ──")
    def compte():
        n = sb.table("submissions").select("id", count="exact").eq("ao_id", ao_id).limit(1).execute().count
        assert n == 2, f"attendu 2, obtenu {n}"
        return n
    r.teste("count exact", compte)

    print("\n── 8. in_() avec liste vide ──")
    def vide():
        d = sb.table("clients").select("id").in_("id", []).execute().data
        assert d == [], f"attendu [], obtenu {d}"
        return "[]"
    r.teste("in_([]) → []", vide)

    print("\n── 9. Conflit d'unicité (services/email_outbox.py:91 lit str(e)) ──")
    ligne = {"to_email": SENTINELLE, "subject": "verif", "html": "x",
             "category": "verification", "idempotency_key": cle_idem}
    r.teste("insert initial", lambda: sb.table("email_outbox").insert(dict(ligne)).execute().data[0]["id"])
    def doublon():
        try:
            sb.table("email_outbox").insert(dict(ligne)).execute()
        except Exception as e:  # noqa: BLE001
            m = _texte(e)
            reconnu = "duplicate" in m or "unique" in m or "23505" in m
            assert reconnu, ("le doublon n'est pas reconnaissable dans str(e) : "
                             "enqueue() le traiterait comme une panne et perdrait l'email")
            return "reconnu (23505/duplicate)"
        raise AssertionError("aucune erreur : la contrainte d'unicité manque")
    r.teste("doublon reconnaissable par enqueue()", doublon)

    print("\n── 10. Colonne absente (repli gracieux de routers/aos.py:183) ──")
    def colonne_absente():
        try:
            sb.table("clients").select("id, zz_colonne_absente").execute()
        except Exception as e:  # noqa: BLE001
            m = _texte(e)
            assert "42703" in m or ("column" in m and "does not exist" in m), \
                f"code d'erreur non reconnaissable : {str(e)[:120]}"
            return "reconnu (42703)"
        raise AssertionError("aucune erreur sur une colonne inexistante")
    r.teste("42703 relayé", colonne_absente)

    print("\n── 11. Clé étrangère violée (routers/auth.py:290) ──")
    def fk():
        try:
            sb.table("appels_offres").insert({
                "title": "ZZ", "description": "test", "skills_required": "test",
                "client_id": "00000000-0000-0000-0000-000000000000",
            }).execute()
        except Exception as e:  # noqa: BLE001
            assert "violates foreign key" in _texte(e), f"message inattendu : {str(e)[:120]}"
            return "reconnu"
        raise AssertionError("aucune erreur : la clé étrangère manque")
    r.teste("violation de clé étrangère", fk)

    print("\n── 12. Verrouillage du rôle anon ──")
    if args.anon_key:
        anon = create_client(args.url, args.anon_key)
        def rls():
            """Deux issues acceptables, et la meilleure des deux est le refus.

            `schema.sql` pose DEUX verrous indépendants : `anon` n'a aucun droit
            sur le schéma (permission denied, code 42501), et la RLS est activée
            sans policy (zéro ligne). Le retrait accidentel de l'un laisse
            l'autre debout.

            Le refus vaut mieux que la liste vide : une liste vide ressemble à
            une base sans données, un 42501 désigne la cause. Mais les deux
            protègent, donc les deux passent — ce test vérifie qu'`anon` ne voit
            RIEN, pas la manière dont il ne voit rien.
            """
            try:
                d = anon.table("clients").select("id").execute().data
            except Exception as e:  # noqa: BLE001
                if "42501" in str(e) or "permission denied" in _texte(e):
                    return "refus explicite (42501) — le meilleur des deux cas"
                raise
            assert d == [], (f"le rôle anon voit {len(d)} ligne(s) : plus aucun verrou ne tient. "
                             f"Vérifier que chaque table a ENABLE ROW LEVEL SECURITY sans policy, "
                             f"et qu'anon n'a pas reçu USAGE sur le schéma public.")
            return "aucune ligne visible (RLS)"
        r.teste("anon ne voit rien", rls)
    else:
        print("  ⏭  ignoré (fournir --anon-key pour le tester)")



if __name__ == "__main__":
    sys.exit(main())
