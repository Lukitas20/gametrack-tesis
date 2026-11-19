"""Integridad y privacidad del artefacto real; no ejecuta entrenamiento."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

import pytest

BACKEND = Path(__file__).resolve().parents[1]


def verifier():
    spec = importlib.util.spec_from_file_location(
        "verify_steam_affinity_model", BACKEND / "scripts" / "verify_steam_affinity_model.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bundled_affinity_has_audited_real_provenance():
    result = verifier().verify_artifact()
    assert result["status"] == "verified"
    assert result["source"] == "steam_public_review_api"
    assert result["data_policy"] == "observed-only-v1"
    assert result["eligible_heads"]


def test_changed_artifact_is_rejected_before_inference(tmp_path):
    model = tmp_path / "steam_affinity.json"
    shutil.copyfile(BACKEND / "models" / "steam_affinity.json", model)
    with model.open("ab") as handle:
        handle.write(b"\n")
    with pytest.raises(ValueError, match="sin auditar o modificado"):
        verifier().verify_artifact(model)


@pytest.mark.parametrize("failure", ["synthetic_source", "private_author_array", "no_eligible_head", "invalid_weights"])
def test_unpublishable_payload_fails_even_with_a_recomputed_hash(tmp_path, monkeypatch, failure):
    """A digest alone must not turn invalid/prohibited data into an approved model."""
    module = verifier()
    payload = json.loads((BACKEND / "models" / "steam_affinity.json").read_text(encoding="utf-8"))
    if failure == "synthetic_source":
        payload["metadata"]["source"] = "synthetic"
    elif failure == "private_author_array":
        payload["user_keys"] = ["unit-test-only"]
    elif failure == "no_eligible_head":
        for head in payload["metadata"]["heads"].values():
            head["automatic_eligible"] = False
    else:
        payload["heads"]["explicit"]["feature_scale"][0] = 0
    model = tmp_path / "steam_affinity.json"
    model.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(module, "EXPECTED_SHA256", hashlib.sha256(model.read_bytes()).hexdigest())
    with pytest.raises(ValueError):
        module.verify_artifact(model)
