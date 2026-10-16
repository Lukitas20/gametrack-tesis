"""El preflight debe impedir que corpus, secretos o modelos demo lleguen al build."""
import importlib.util
from pathlib import Path

import pytest


def script():
    path = Path(__file__).resolve().parents[2] / "deploy" / "google-cloud" / "verify_source_upload.py"
    spec = importlib.util.spec_from_file_location("verify_source_upload", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_two_real_models_are_complete_and_publishable():
    module = script()
    result = module.audit_paths(module.REQUIRED | {"backend/app/main.py", "frontend/index.html"})
    assert result["status"] == "verified" and result["private_paths"] == 0
    assert result["models"] == sorted(module.ALLOWED_MODELS)


@pytest.mark.parametrize("private", [
    "backend/.env", "backend/custom/observations.jsonl", "backup/.author_hmac.key",
    "backend/data/generated/manifest.json", "backend/models/local_model.npz",
    "backend/models/other.json", "deploy/google-cloud/current.tfstate.backup",
    "deploy/google-cloud/settings.auto.tfvars.json", "source/gametrack.db-wal",
    "deploy/google-cloud/.tools/gcloud-config/application_default_credentials.json",
    "frontend/tests/experience-onboarding.test.mjs",
])
def test_private_or_unapproved_files_stop_publication(private):
    module = script()
    with pytest.raises(ValueError, match="privadas o artefactos no aprobados"):
        module.audit_paths(module.REQUIRED | {private})


def test_missing_affinity_weights_stop_publication():
    module = script()
    with pytest.raises(ValueError, match="Faltan verificadores o modelos"):
        module.audit_paths(module.REQUIRED - {"backend/models/steam_affinity.json"})
