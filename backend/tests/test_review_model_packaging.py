"""Audita los artefactos reales que se empaquetan, sin entrenar ni escribir datos."""
import importlib.util
from pathlib import Path
import shutil

import pytest

BACKEND = Path(__file__).resolve().parents[1]


def verifier():
    spec = importlib.util.spec_from_file_location("verify_review_model", BACKEND / "scripts" / "verify_review_model.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bundled_real_weights_have_verified_provenance():
    result = verifier().verify_artifact()
    assert result["status"] == "verified"
    assert result["source"] == "real_steam_reviews"
    assert result["examples"] == 24000


def test_modified_weights_are_rejected_before_loading(tmp_path):
    model = tmp_path / "review_model.npz"
    shutil.copyfile(BACKEND / "models" / "review_model.npz", model)
    shutil.copyfile(BACKEND / "models" / "review_model.json", model.with_suffix(".json"))
    with model.open("ab") as handle:
        handle.write(b"unapproved-change")
    with pytest.raises(ValueError, match="sin auditar o modificado"):
        verifier().verify_artifact(model)
