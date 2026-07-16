-- 0006 — Motif de refus d'un candidat (« Non retenu »), au niveau candidat.
--
-- Lève l'effet « boîte noire » côté partenaire : quand le staff marque un profil
-- « Non retenu », il saisit un motif court (pré-rempli par l'IA, ajustable dans une
-- liste). Ce motif est communiqué au partenaire porteur — et uniquement à lui,
-- uniquement pour SES profils écartés (voir routers/submissions.py).
--
-- Colonne unique, best-effort côté code : tant que cette migration n'est pas
-- appliquée, le backend dégrade proprement (l'upsert/select retombe sans elle).
alter table public.ao_consultant_state
  add column if not exists refusal_reason text;   -- motif communiqué au partenaire (NULL si non renseigné / profil retenu)
