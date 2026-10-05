// Initialisation MIP RUM (Real User Monitoring). Fichier externe pour CSP
// stricte (script-src sans 'unsafe-inline') — voir vercel.json.
// NB : la clé apiKey est volontairement côté client (ingestion write-only) ;
// elle ne donne aucun accès en lecture. Si le fournisseur MIP permet de la
// faire tourner, préférer une rotation périodique.
(function () {
  if (typeof MIPRum === 'undefined') return; // script CDN bloqué/indisponible : l'app vit sans RUM
  MIPRum.init({
    // Ingestion redéployée sur Vercel (août 2026). L'ancienne adresse était une
    // edge function du projet Supabase nupxrdpsliqptqnjkmgw, supprimé depuis :
    // tout ce qui l'appelle échoue désormais, silencieusement — la télémétrie
    // est best-effort et ne remonte aucune erreur à l'utilisateur.
    endpoint: "https://mip-rum-console.vercel.app/api/ingest/v1/traces",
    appId: "gip-plateforme",
    clientId: "groupement-it",
    apiKey: "mip_4ecfa86115cf090d0b358b340108139f", // générée le 29/09/2026 ; l'ancienne était publique et jamais enregistrée
    env: "production",
    // mip:release — remplacée au build par la release (le commit déployé) : scripts/mip-sourcemaps.mjs
    sampleRate: 1.0,   // 100 % des sessions (traces techniques, sans PII)
    trace: true,       // tracing distribué front→back (spans serveur déjà collectés)
    // Rejeu de session ACTIF sur toutes les sessions, sans bannière : décision du
    // gestionnaire d'UTI du 05/10/2026, qui en porte la base légale. Masquage par
    // défaut du SDK (« all ») : texte, saisies et images partent masqués — les noms
    // de consultants et de clients n'apparaissent pas dans les rejeux.
    replay: 1.0,
    frustration: true, // rage clicks / dead clicks
    // WIDGET D'AVIS MIP (CSAT) : RETIRÉ. Ne pas le remettre.
    //
    // Il a été introduit pour alimenter le CSAT par fonction de la Supervision
    // IA, puis corrigé deux fois sans jamais répondre à la vraie objection :
    // ced98d1 l'a cantonné aux pages authentifiées, da5fec7 l'a décollé du
    // bouton Assistant. Or les pages authentifiées SONT les pages où l'équipe
    // travaille toute la journée : le restreindre revenait à le laisser
    // exactement là où il gênait. Demandé retiré à plusieurs reprises.
    //
    // Le SDK ne l'initialise que si la clé est présente et vraie
    // (`t.feedback && zr(t.feedback)` dans mip-rum.js) : l'absence de clé
    // suffit, il n'y a rien à désactiver ailleurs. Le reste de la télémétrie
    // — traces, spans, frustration — continue de remonter normalement.
    //
    // Si le CSAT redevient un besoin, il se collecte dans l'application, à un
    // moment choisi par nous, pas par une bulle flottante permanente.
  });
})();
