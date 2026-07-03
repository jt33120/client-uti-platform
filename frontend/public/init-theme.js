// Applique le thème avant le premier paint (évite le flash clair→sombre).
// Fichier externe (et non script inline) pour permettre une CSP stricte
// (script-src sans 'unsafe-inline') — voir vercel.json.
(function () {
  try {
    var t = localStorage.getItem('theme');
    if (t === 'dark') document.documentElement.classList.add('dark');
  } catch (e) {}
})();
