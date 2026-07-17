// Initialisation MIP RUM (Real User Monitoring). Fichier externe pour CSP
// stricte (script-src sans 'unsafe-inline') — voir vercel.json.
// NB : la clé apiKey est volontairement côté client (ingestion write-only) ;
// elle ne donne aucun accès en lecture. Si le fournisseur MIP permet de la
// faire tourner, préférer une rotation périodique.
(function () {
  if (typeof MIPRum === 'undefined') return; // script CDN bloqué/indisponible : l'app vit sans RUM
  MIPRum.init({
    endpoint: "https://nupxrdpsliqptqnjkmgw.supabase.co/functions/v1/v1-traces",
    appId: "gip-plateforme",
    clientId: "groupement-it",
    apiKey: "mip_live_gip_59ca708ce596d20a52d58ea976dc512c516b442a",
    env: "production",
    sampleRate: 1.0,   // 100 % des sessions (traces techniques, sans PII)
    trace: true,       // tracing distribué front→back (spans serveur déjà collectés)
    // Session-replay DÉSACTIVÉ : il capture le DOM affiché (noms de consultants,
    // clients — des données personnelles) et la bannière cookies actuelle est
    // informative, sans gate de consentement (RGPD). Pour le réactiver :
    // ajouter requireConsent:true + appeler MIPRum.consent(true) depuis la
    // bannière, puis remettre replay: 0.1.
    replay: 0,
    frustration: true, // rage clicks / dead clicks
    // Widget d'avis MIP (CSAT) : alimente le CSAT par fonction dans la
    // Supervision IA. Aucun script séparé à poser (SDK ≥ 0.4.2 ; ignoré en
    // silence si la version ne le supporte pas).
    // Cantonné aux SECTIONS AUTHENTIFIÉES (onlyPaths = préfixes des routes sous
    // ProtectedRoute) : on ne sollicite JAMAIS d'avis sur la page publique de
    // retour client (/client-review/:token), le login ou les pages légales.
    // Le widget se masque/réapparaît tout seul à la navigation (SPA comprise).
    feedback: {
      onlyPaths: [
        '/dashboard', '/clients', '/consultants', '/aos', '/partners',
        '/graph', '/carte', '/pacs', '/emails', '/notifications',
        '/admin', '/supervision', '/tickets'
      ]
    }
  });
})();
