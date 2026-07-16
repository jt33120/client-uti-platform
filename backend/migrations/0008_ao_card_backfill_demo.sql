-- Backfill démo : complète les champs affichés sur la carte AO (AOCard, front)
-- pour les AO déjà PUBLIÉS (is_draft = false) créés avant que ces champs ne
-- deviennent requis à la publication (cf. backend/routers/aos.py,
-- _PUBLISH_REQUIRED_FIELDS). Idempotent : ne touche que les colonnes vides,
-- et ne modifie jamais un brouillon (is_draft = true), qui reste
-- volontairement incomplet tant qu'il n'est pas publié.
--
-- Valeurs dérivées de façon déterministe de l'id (hashtext) : ce sont des
-- bouchons d'affichage pour homogénéiser la démo sur les AO historiques,
-- pas des données réelles — à ne pas utiliser pour du reporting métier.

update public.appels_offres
set reference = 'AO-' || upper(substr(replace(id::text, '-', ''), 1, 8))
where is_draft = false and (reference is null or btrim(reference) = '');

update public.appels_offres
set ao_type = (array['Assurance','Banque / Finance','IT / Dev','Énergie','Retail','Public','Santé','Autre'])
              [1 + (abs(hashtext(id::text)::bigint) % 8)]
where is_draft = false and (ao_type is null or btrim(ao_type) = '');

update public.appels_offres
set location = (array['Paris','Lyon','Nantes','Lille','Toulouse','Bordeaux','Marseille','Full remote'])
               [1 + (abs(hashtext(id::text)::bigint) % 8)]
where is_draft = false and (location is null or btrim(location) = '');

update public.appels_offres
set duration = (array['3 mois renouvelable','6 mois renouvelable','12 mois renouvelable','Mission longue durée','3 mois','9 mois renouvelable'])
               [1 + (abs(hashtext(id::text)::bigint) % 6)]
where is_draft = false and (duration is null or btrim(duration) = '');

update public.appels_offres
set budget_max = 450 + (abs(hashtext(id::text)::bigint) % 9) * 50
where is_draft = false and budget_max is null;

-- Échéance : AO ouverts non archivés -> une échéance à venir (démo
-- "vivante") ; AO fermés/archivés -> une échéance passée cohérente avec leur
-- ancienneté (created_at + 30 j) — parfaitement normal pour un AO déjà clos.
update public.appels_offres
set deadline = case
    when status = 'open' and not coalesce(archived, false)
      then (greatest(current_date, created_at::date) + interval '21 days')::date
    else (created_at::date + interval '30 days')::date
  end
where is_draft = false and deadline is null;
