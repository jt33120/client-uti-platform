"""
Repli LLM de la génération d'AO : si OpenRouter est indisponible (ex. clé
révoquée → 401 « User not found »), la génération doit basculer sur Mistral au
lieu d'échouer sèchement.
"""
import os
import types

import pytest

# Variables minimales pour que `config` s'importe hors environnement serveur.
for k, v in {
    "SUPABASE_URL": "http://x", "SUPABASE_SERVICE_KEY": "x", "SUPABASE_ANON_KEY": "x",
    "JWT_SECRET": "x", "SECRET_KEY": "x",
}.items():
    os.environ.setdefault(k, v)

from services import ao_drafter  # noqa: E402

_GOOD_JSON = (
    '{"title":"Admin plan de charge Sénior",'
    '"reference":"Marché Spécifique n°23915SA240MS","description":"x",'
    '"skills_required":"Clarity, Excel","ao_type":"","budget_max":null,'
    '"location":"Paris 12","duration":"24 mois","deadline":"2026-08-14",'
    '"context":"y","importance":{"competences":4,"seniorite":5,"contexte":3,"tjm":3}}'
)


def _resp(content):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content))]
    )


def _client(behavior):
    async def create(**_kw):
        return behavior()

    return types.SimpleNamespace(chat=types.SimpleNamespace(
        completions=types.SimpleNamespace(create=create)))


def _raise_401():
    raise Exception("Error code: 401 - {'error': {'message': 'User not found.', 'code': 401}}")


@pytest.mark.asyncio
async def test_falls_back_to_mistral_on_openrouter_401(monkeypatch):
    monkeypatch.setattr(ao_drafter, "_client", _client(_raise_401))
    monkeypatch.setattr(ao_drafter, "_mistral_client", _client(lambda: _resp(_GOOD_JSON)))
    out = await ao_drafter.draft_ao_fields("source", ["Assurance", "Banque / Finance"])
    assert out is not None
    assert out["reference"].startswith("Marché Spécifique")
    assert out["deadline"] == "2026-08-14"


@pytest.mark.asyncio
async def test_raises_when_all_providers_down(monkeypatch):
    monkeypatch.setattr(ao_drafter, "_client", _client(_raise_401))
    monkeypatch.setattr(ao_drafter, "_mistral_client", _client(_raise_401))
    with pytest.raises(Exception):
        await ao_drafter.draft_ao_fields("source", ["Assurance"])


def test_available_with_only_mistral(monkeypatch):
    monkeypatch.setattr(ao_drafter, "_client", None)
    monkeypatch.setattr(ao_drafter, "_mistral_client", _client(lambda: _resp(_GOOD_JSON)))
    assert ao_drafter.is_available() is True


def test_ai_suggestion_never_reenables_tjm_scoring():
    # Grille v2.2 : le TJM est hors scoring. Même si le modèle renvoie une
    # importance "tjm" (cf. _GOOD_JSON), elle ne doit PAS être reprise —
    # sinon chaque AO généré par l'IA réactiverait le critère en silence.
    stars = ao_drafter._stars({"competences": 4, "seniorite": 5, "contexte": 3, "tjm": 3})
    assert "tjm" not in stars
    assert stars["competences"] == 4
