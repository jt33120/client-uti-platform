import os
import sys
import types

# Make the backend package importable (`import services.scoring`) regardless of
# the directory pytest is invoked from.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Config minimale : les modules de services importent `config.settings` au chargement.
os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")

# `supabase` n'est pas toujours installé hors CI. On ne pose le bouchon QUE s'il
# manque : là où le vrai paquet existe (CI, prod), rien n'est masqué — un import
# réellement cassé continue de faire échouer la collecte.
try:  # pragma: no cover - dépend de l'environnement
    import supabase  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    _stub = types.ModuleType("supabase")
    _stub.create_client = lambda *a, **k: None
    _stub.Client = object
    sys.modules["supabase"] = _stub
