// MIP RUM — source maps, après `vite build`.
//
// Sans source map, une erreur de production se lit `at e (index-K_Wae3fl.js:1:40)` dans
// la console MIP ; avec, elle pointe la ligne de `src/`. Trois gestes :
//   1. la release (le commit déployé, VERCEL_GIT_COMMIT_SHA) est écrite dans
//      dist/init-mip-rum.js, à la place de la ligne `// mip:release` : MIP rapproche
//      une erreur de sa map par cette valeur, à l'octet près ;
//   2. les maps sont envoyées à MIP si MIP_SOURCEMAP_TOKEN est posé (jeton `msu_…`,
//      console MIP → Administration → Source maps → Jetons de CI) ;
//   3. les maps sont TOUJOURS retirées de dist/ : le code source ne se sert pas au public.
//
// Ce script n'échoue jamais le build : la télémétrie ne doit pas empêcher un déploiement.
import { readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const DIST = new URL("../dist/", import.meta.url).pathname;
const APP_ID = "gip-plateforme";
const URL_ENVOI = process.env.MIP_SOURCEMAP_URL ?? "https://mip-rum-console.vercel.app/api/sourcemaps";
const release = (process.env.VERCEL_GIT_COMMIT_SHA ?? "").slice(0, 120);
const jeton = process.env.MIP_SOURCEMAP_TOKEN;

const maps = (dossier) =>
  readdirSync(dossier, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? maps(join(dossier, e.name)) : e.name.endsWith(".js.map") ? [join(dossier, e.name)] : []);

async function principal() {
  const fichiers = maps(DIST);

  if (release) {
    const init = join(DIST, "init-mip-rum.js");
    const texte = readFileSync(init, "utf8");
    if (texte.includes("// mip:release")) {
      writeFileSync(init, texte.replace(/^.*\/\/ mip:release.*$/m, `    release: ${JSON.stringify(release)},`));
      console.log(`[mip] release ${release} écrite dans init-mip-rum.js`);
    }
  }

  if (jeton && release) {
    for (const map of fichiers) {
      const filename = map.split("/").pop().replace(/\.map$/, "");
      try {
        const reponse = await fetch(URL_ENVOI, {
          method: "POST",
          headers: { authorization: `Bearer ${jeton}`, "content-type": "application/json" },
          body: JSON.stringify({ appId: APP_ID, release, maps: [{ filename, content: JSON.parse(readFileSync(map, "utf8")) }] }),
        });
        console.log(`[mip] source map ${filename} : ${reponse.status}`);
      } catch (e) {
        console.log(`[mip] source map ${filename} : échec (${e.message})`);
      }
    }
  } else {
    console.log(`[mip] source maps non envoyées (${!jeton ? "MIP_SOURCEMAP_TOKEN absent" : "release inconnue hors de Vercel"})`);
  }

  for (const map of fichiers) rmSync(map);
  console.log(`[mip] ${fichiers.length} source map(s) retirée(s) de dist/`);
}

try {
  await principal();
} catch (e) {
  console.log(`[mip] étape source maps ignorée : ${e.message}`);
}
