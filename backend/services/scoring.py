"""
Étape 2 du pipeline — SCORING DÉTERMINISTE (aucune IA).

Conformité AI Act : le score est calculé ici par une formule **explicite,
versionnée et reproductible** (Art. 13 transparence, Art. 15 reproductibilité),
sur des **features justifiables** (Art. 10 — pas de texte brut porteur de biais).

Grille v2.1 (total 100, poids par défaut dérivés des étoiles) :
  competences_techniques  : ~37 — recouvrement compétences requises ∩ candidat
                                  (matching par synonymes/alias, cf. _SKILL_ALIASES)
  seniorite               : ~18 — années d'expérience vs cible
  contexte_domaine        : ~18 — adéquation secteur/contexte de l'AO
  points_forts_cv         : ~9  — force des points forts du CV (noté par l'IA)
  elements_differenciants : ~9  — ce qui distingue le profil (noté par l'IA)
  compatibilite_tjm       : ~9  — TJM consultant vs budget de l'AO (0★ = exclu)

Les deux axes qualitatifs (points_forts_cv / elements_differenciants) n'ont pas
de signal déterministe : la grille les pose en NEUTRE et le 2e avis IA
(services.llm_scoring) les note réellement. Un critère mis à 0★ est retiré du
score (poids nul), utile p. ex. pour le TJM déjà borné par le TJM max de l'AO.

⚠️ Les seuils ci-dessous sont des VALEURS PAR DÉFAUT, à valider par le métier
(cf. compliance/ai-act/phase-3-technique/02-spec-architecture-hybride.md).
Toute modification doit incrémenter GRID_VERSION (Art. 17 — gestion des
modifications) et déclencher les tests de biais.
"""
from __future__ import annotations
import re
import unicodedata
from typing import Optional

GRID_VERSION = "2.1.0"

# Poids de la grille (somme = 100). NB : depuis v2 la forme canonique est
# « en étoiles » (cf. plus bas) ; ces poids par défaut en sont DÉRIVÉS.
W_COMPETENCES = 40
W_SENIORITE = 20
W_CONTEXTE = 20
W_TJM = 20

SENIORITY_FULL_YEARS = 8   # années d'XP pour le score séniorité maximal
RECO_FORT_MIN = 75         # score total >= FORT
RECO_MOYEN_MIN = 50        # score total >= MOYEN
NEUTRAL_RATIO = 0.5        # ratio neutre appliqué quand une donnée manque
STRONG_RATIO = 0.75        # ratio d'un critère => point fort
WEAK_RATIO = 0.40          # ratio d'un critère => point faible

# Configuration par défaut de la grille. Ces valeurs sont pilotables depuis un
# compte admin (table scoring_config + page « Paramètres scoring ») ; toute
# valeur fournie via `config` surcharge le défaut correspondant. Garder la
# traçabilité : un changement de config est journalisé (Art. 12).
DEFAULTS = {
    # Les poids w_* sont complétés/dérivés des étoiles par défaut plus bas
    # (source unique de vérité), pour rester cohérents avec DEFAULT_STARS.
    "seniority_full_years": SENIORITY_FULL_YEARS,
    "reco_fort_min": RECO_FORT_MIN,
    "reco_moyen_min": RECO_MOYEN_MIN,
}

# ── Importance « en étoiles » (0-5) ────────────────────────────────────────
# Forme pilotée par l'UI pour des utilisateurs non techniques : on note
# l'importance RELATIVE de chaque critère (0★ = exclu, 1★ = accessoire …
# 5★ = critique) et les poids w_* (somme = 100) en sont DÉRIVÉS par
# normalisation. Cela supprime la contrainte « la somme doit faire 100 » côté
# écran. Mettre un critère à 0★ le RETIRE totalement du score (utile p. ex.
# pour le TJM, déjà borné par le TJM max de l'AO).
#
# v2 : deux axes qualitatifs supplémentaires — « points forts du CV » et
# « éléments différenciants ». Ils n'ont pas de signal déterministe fiable
# (jugement qualitatif) : la grille pose un socle neutre et le 2e avis IA
# (services.llm_scoring) les note réellement.
STAR_CRITERIA = (
    "competences", "seniorite", "contexte",
    "points_forts_cv", "elements_differenciants", "tjm",
)
# v2.1 : les deux axes qualitatifs (points_forts_cv / elements_differenciants)
# n'ont PAS de signal déterministe — la grille les pose au neutre (0.5) et
# seule l'IA les note vraiment. À 2★ chacun (v2.0) ils gelaient ~30/100 points
# au milieu du barème, ce qui aspirait mécaniquement tout score vers ~55-60 %.
# Ramenés à 1★ : moins de « poids mort » neutre, davantage de points rendus aux
# axes réellement mesurables (compétences/séniorité/contexte). Reste pilotable
# par AO/admin (table scoring_config) — c'est un défaut, pas un plafond.
DEFAULT_STARS = {
    "competences": 4, "seniorite": 2, "contexte": 2,
    "points_forts_cv": 1, "elements_differenciants": 1, "tjm": 1,
}
# Correspondance clé étoile → clé du breakdown déterministe (services.scoring)
# et libellé humain (explicabilité). Ordre = ordre d'affichage.
CRITERIA_META = (
    ("competences", "competences_techniques", "compétences techniques"),
    ("seniorite", "seniorite", "séniorité"),
    ("contexte", "contexte_domaine", "adéquation au contexte"),
    ("points_forts_cv", "points_forts_cv", "points forts du CV"),
    ("elements_differenciants", "elements_differenciants", "éléments différenciants"),
    ("tjm", "compatibilite_tjm", "compatibilité TJM"),
)
STAR_MIN, STAR_MAX = 0, 5


def _clamp_star(v) -> Optional[int]:
    try:
        v = int(v)
    except (TypeError, ValueError):
        return None
    return max(STAR_MIN, min(STAR_MAX, v))


def normalize_stars(stars: dict | None) -> dict:
    """Borne (1-5) et complète un dict d'étoiles sur les 4 critères."""
    out = {}
    for c in STAR_CRITERIA:
        v = _clamp_star((stars or {}).get(c))
        out[c] = v if v is not None else DEFAULT_STARS[c]
    return out


def stars_to_weights(stars: dict | None) -> dict:
    """
    Convertit des étoiles d'importance (0-5) en poids entiers dont la somme fait
    EXACTEMENT 100. La méthode du plus fort reste absorbe les arrondis, de sorte
    que le total est garanti à 100 et que le scoring reste déterministe.

    Un critère à 0★ est EXCLU : il reçoit un poids nul et n'entre pas dans la
    répartition (les 100 points se distribuent sur les seuls critères ≥ 1★).
    Si tous les critères sont à 0★, on retombe sur une répartition uniforme.
    """
    s = normalize_stars(stars)
    active = [c for c in STAR_CRITERIA if s[c] > 0]
    if not active:  # garde-fou : aucune importance renseignée
        active = list(STAR_CRITERIA)
        s = {c: 1 for c in STAR_CRITERIA}
    total = sum(s[c] for c in active) or 1
    raw = {c: (s[c] / total * 100 if c in active else 0.0) for c in STAR_CRITERIA}
    floor = {c: int(raw[c]) for c in STAR_CRITERIA}
    remainder = 100 - sum(floor.values())
    for c in sorted(active, key=lambda k: raw[k] - floor[k], reverse=True)[:remainder]:
        floor[c] += 1
    return {f"w_{c}": floor[c] for c in STAR_CRITERIA}


# Poids par défaut = dérivés des étoiles par défaut (source unique de vérité).
DEFAULTS.update(stars_to_weights(DEFAULT_STARS))

_SPLIT = re.compile(r"[,;/|\n]+")
# Mots vides FR/EN les plus courants, écartés des signaux de contexte.
_STOPWORDS = {
    "le", "la", "les", "un", "une", "des", "de", "du", "et", "ou", "en", "au",
    "aux", "pour", "avec", "sur", "dans", "par", "sans", "the", "and", "for",
    "with", "of", "to", "in", "on", "a", "an", "ans", "an",
}


# ── Synonymes de compétences (v2.1) ────────────────────────────────────────
# Le matching de compétences était purement lexical : « React.js » ≠ « ReactJS »,
# « K8s » ≠ « Kubernetes », « JS » ≠ « JavaScript »… → un consultant réellement
# compétent perdait des points sur l'axe le plus lourd à cause de l'orthographe.
# On canonicalise via une table d'alias CURÉE (déterministe, auditable, sans IA)
# appliquée aux compétences requises ET candidates avant comparaison. Conservateur
# à dessein : uniquement des équivalences non ambiguës et largement admises.
_SKILL_ALIASES = {
    "reactjs": "react", "react.js": "react", "react js": "react",
    "vuejs": "vue", "vue.js": "vue", "vue js": "vue",
    "nodejs": "node", "node.js": "node", "node js": "node",
    "nextjs": "next", "next.js": "next", "next js": "next",
    "nestjs": "nest", "nest.js": "nest",
    "js": "javascript", "ts": "typescript",
    "py": "python", "golang": "go",
    "k8s": "kubernetes", "postgres": "postgresql", "postgre": "postgresql",
    "psql": "postgresql", "mongo": "mongodb", "es": "elasticsearch",
    "csharp": "c#", "c sharp": "c#", "dotnet": ".net", ".net core": ".net",
    "gcp": "google cloud", "aws": "amazon web services",
    "tf": "terraform", "k8": "kubernetes",
}


def _canon(tok: str) -> str:
    """Ramène un libellé de compétence normalisé à sa forme canonique (alias)."""
    return _SKILL_ALIASES.get(tok, tok)


def _norm(s: str) -> str:
    """Minuscule, sans accents, espaces normalisés."""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


def _tokens(value: Optional[str]) -> list[str]:
    """Découpe une chaîne (compétences/contexte) en tokens normalisés utiles."""
    if not value:
        return []
    parts = _SPLIT.split(str(value))
    out: list[str] = []
    for p in parts:
        t = _norm(p)
        # Pas de filtre de longueur : la découpe est par virgule/ligne, donc un
        # token court est un vrai libellé (« Go », « C# », « R », « BI ») — le
        # bruit est écarté par la liste de mots vides.
        if t and t not in _STOPWORDS:
            out.append(t)
    return out


def _match(needle: str, haystack: set[str]) -> bool:
    """Correspondance lâche : égalité ou inclusion mutuelle (ex. 'react' ⊂ 'react.js').
    L'inclusion est réservée aux libellés de plus de 3 caractères : sur un
    libellé court elle fabrique des faux positifs (« r » ⊂ « docker »,
    « go » ⊂ « django ») — un skill court ne matche qu'à l'exact."""
    for h in haystack:
        if needle == h:
            return True
        if len(needle) > 3 and len(h) > 3 and (needle in h or h in needle):
            return True
    return False


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _reco(total: int, fort_min: int, moyen_min: int) -> str:
    if total >= fort_min:
        return "FORT"
    if total >= moyen_min:
        return "MOYEN"
    return "FAIBLE"


def score_consultant(features: dict, consultant: dict, ao: dict, config: dict | None = None) -> dict:
    """
    Calcule le score d'un consultant pour un AO, de façon 100 % déterministe.

    `features`   : sortie de l'extraction LLM (peut être vide -> fallback déclaré).
    `consultant` : données déclarées (skills, tjm, experience_years).
    `ao`         : appel d'offres (skills_required, budget_max, ao_type, context...).
    `config`     : surcharge optionnelle de la grille (poids/seuils), pilotée par
                   l'admin. Les clés absentes retombent sur DEFAULTS.

    Retourne un dict compatible avec la table `matchings` :
    score_total, breakdown, points_forts, points_faibles, resume_matching, recommandation.
    """
    features = features or {}
    cfg = {**DEFAULTS, **{k: v for k, v in (config or {}).items() if v is not None}}
    # Les étoiles d'importance (si fournies) sont la forme canonique pilotée par
    # l'UI : elles priment sur d'éventuels poids w_* et sont normalisées à 100.
    stars = (config or {}).get("stars")
    if stars:
        cfg.update(stars_to_weights(stars))
    w_comp = cfg["w_competences"]
    w_sen = cfg["w_seniorite"]
    w_ctx = cfg["w_contexte"]
    w_pf = cfg["w_points_forts_cv"]
    w_ed = cfg["w_elements_differenciants"]
    w_tjm = cfg["w_tjm"]
    seniority_full = cfg["seniority_full_years"] or SENIORITY_FULL_YEARS

    # ── Compétences (poids le plus lourd) ──────────────────────────
    # Canonicalisation par alias (v2.1) des deux côtés → « ReactJS » matche
    # « React.js », « K8s » matche « Kubernetes », etc. (déterministe, auditable).
    required = [_canon(t) for t in _tokens(ao.get("skills_required"))]
    candidate_skills = {_canon(_norm(s)) for s in features.get("skills", [])}
    candidate_skills |= {_canon(t) for t in _tokens(consultant.get("skills"))}
    if not required:
        comp_ratio = NEUTRAL_RATIO
    elif not candidate_skills:
        comp_ratio = 0.0
    else:
        matched = [r for r in required if _match(r, candidate_skills)]
        comp_ratio = len(matched) / len(required)
    comp_score = round(w_comp * _clamp01(comp_ratio))

    # ── Séniorité (20) ─────────────────────────────────────────────
    years = features.get("experience_years")
    if years is None:
        years = consultant.get("experience_years")
    if years is None:
        sen_ratio = NEUTRAL_RATIO
    else:
        sen_ratio = _clamp01(max(years, 0) / seniority_full)
    sen_score = round(w_sen * sen_ratio)

    # ── Contexte / secteur (20) ────────────────────────────────────
    ctx_signals = (
        _tokens(ao.get("ao_type"))
        + _tokens(ao.get("context"))
        + _tokens(ao.get("title"))
    )
    cand_ctx = {_norm(s) for s in features.get("sectors", [])}
    cand_ctx |= set(_tokens(features.get("summary")))
    cand_ctx |= set(_tokens(consultant.get("skills")))
    if not ctx_signals:
        ctx_ratio = NEUTRAL_RATIO
    else:
        hits = [t for t in ctx_signals if _match(t, cand_ctx)]
        # Lâche : retrouver la moitié des signaux suffit pour le score plein.
        ctx_ratio = _clamp01(len(hits) / len(ctx_signals) * 2)
    ctx_score = round(w_ctx * ctx_ratio)

    # ── Points forts du CV / Éléments différenciants (qualitatifs) ─
    # Aucun signal déterministe fiable (jugement qualitatif) : la grille pose
    # un socle NEUTRE et laisse le 2e avis IA (services.llm_scoring) trancher.
    # Le score hybride est alors l'avis IA ancré sur ce neutre ; si l'IA est
    # indisponible, ces axes restent neutres (dégradation maîtrisée, Art. 15).
    pf_ratio = NEUTRAL_RATIO
    ed_ratio = NEUTRAL_RATIO
    pf_score = round(w_pf * pf_ratio)
    ed_score = round(w_ed * ed_ratio)

    # ── Compatibilité TJM (20) ─────────────────────────────────────
    budget = ao.get("budget_max")
    tjm = consultant.get("tjm")
    if not budget or not tjm:
        tjm_ratio = NEUTRAL_RATIO
    elif tjm <= budget:
        tjm_ratio = 1.0
    else:
        tjm_ratio = _clamp01(budget / tjm)
    tjm_score = round(w_tjm * tjm_ratio)

    total = comp_score + sen_score + ctx_score + pf_score + ed_score + tjm_score

    breakdown = {
        "competences_techniques": comp_score,
        "seniorite": sen_score,
        "contexte_domaine": ctx_score,
        "points_forts_cv": pf_score,
        "elements_differenciants": ed_score,
        "compatibilite_tjm": tjm_score,
    }

    # ── Points forts / faibles dérivés des ratios (explicabilité) ──
    # (label, ratio, poids) — un critère à poids nul (désactivé) est ignoré.
    criteria = [
        ("compétences techniques", comp_ratio, w_comp),
        ("séniorité", sen_ratio, w_sen),
        ("adéquation au contexte", ctx_ratio, w_ctx),
        ("points forts du CV", pf_ratio, w_pf),
        ("éléments différenciants", ed_ratio, w_ed),
        ("compatibilité TJM", tjm_ratio, w_tjm),
    ]
    points_forts = [f"Bon niveau : {label}" for label, r, w in criteria if w > 0 and r >= STRONG_RATIO]
    points_faibles = [f"À vérifier : {label}" for label, r, w in criteria if w > 0 and r <= WEAK_RATIO]

    # Résumé : n'affiche que les axes actifs (poids > 0).
    _resume_axes = [
        ("compétences", comp_score, w_comp),
        ("séniorité", sen_score, w_sen),
        ("contexte", ctx_score, w_ctx),
        ("points forts", pf_score, w_pf),
        ("différenciation", ed_score, w_ed),
        ("TJM", tjm_score, w_tjm),
    ]
    _detail = ", ".join(f"{label} {sc}/{w}" for label, sc, w in _resume_axes if w > 0)
    resume = f"Score {total}/100 — {_detail}. Évaluation déterministe (grille v{GRID_VERSION})."

    return {
        "score_total": total,
        "breakdown": breakdown,
        "points_forts": points_forts,
        "points_faibles": points_faibles,
        "resume_matching": resume,
        "recommandation": _reco(total, cfg["reco_fort_min"], cfg["reco_moyen_min"]),
    }
