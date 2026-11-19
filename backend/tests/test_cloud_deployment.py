"""Transferencia y publicación cloud sin servicios reales ni credenciales."""
import importlib.util
import argparse
from pathlib import Path
import sqlite3

import httpx
import pytest

from app.core.config import Settings
from app.workers.train_cloud_model import upload_artifact, validate_version

DEPLOY = Path(__file__).resolve().parents[2] / "deploy" / "google-cloud"


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, DEPLOY / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_snapshot_captures_wal_and_does_not_overwrite(tmp_path):
    snapshot = load_script("snapshot_sqlite")
    source = tmp_path / "source.db"
    target = tmp_path / "copy.db"
    with sqlite3.connect(source) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE ratings (score INTEGER)")
        db.execute("INSERT INTO ratings VALUES (5)")
        db.commit()
        snapshot.create_snapshot(source, target)
        with sqlite3.connect(target) as copied:
            assert copied.execute("SELECT score FROM ratings").fetchall() == [(5,)]
            assert copied.execute("PRAGMA journal_mode").fetchone() == ("delete",)
        db.execute("INSERT INTO ratings VALUES (1)")
        db.commit()
        with pytest.raises(FileExistsError):
            snapshot.create_snapshot(source, target)
        assert db.execute("SELECT COUNT(*) FROM ratings").fetchone() == (2,)
    with sqlite3.connect(target) as copied:
        assert copied.execute("SELECT COUNT(*) FROM ratings").fetchone() == (1,)


def test_cloud_sql_socket_and_pool_configuration():
    url = "postgresql://gametrack:fake@/gametrack?host=%2Fcloudsql%2Fexample%3Aus-central1%3Agametrack-postgres"
    config = Settings(_env_file=None, DATABASE_URL=url, DB_POOL_SIZE=2, DB_MAX_OVERFLOW=0)
    assert config.DATABASE_URL.endswith(url.split("?", 1)[1])
    assert not config.is_sqlite
    assert config.DB_POOL_SIZE + config.DB_MAX_OVERFLOW == 2
    with pytest.raises(ValueError):
        Settings(_env_file=None, DB_POOL_SIZE=0)


@pytest.mark.parametrize("value", ["../other", "a/b", "", "untrained", "v 1"])
def test_model_version_cannot_escape_immutable_prefix(value):
    with pytest.raises(argparse.ArgumentTypeError):
        validate_version(value)


def test_model_upload_is_create_only_and_preserves_payload(tmp_path):
    artifact = tmp_path / "local_model.npz"
    artifact.write_bytes(b"test-artifact")
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"generation": "1"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        upload_artifact(client, bucket="example-models", version="v1", path=artifact, token="fake")
    assert requests[0].url.params["ifGenerationMatch"] == "0"
    assert requests[0].url.params["name"] == "versions/v1/local_model.npz"
    assert requests[0].content == b"test-artifact"


def test_existing_model_version_is_rejected(tmp_path):
    artifact = tmp_path / "local_model.npz"
    artifact.write_bytes(b"new")
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(412))) as client:
        with pytest.raises(httpx.HTTPStatusError):
            upload_artifact(client, bucket="example-models", version="v1", path=artifact, token="fake")


def test_configure_secrets_preserves_existing_values(monkeypatch, capsys):
    script = load_script("configure_secrets")
    monkeypatch.setattr(script, "has_version", lambda *args: True)
    monkeypatch.setattr(script, "gcloud", lambda *args, **kwargs: "fake-token")
    monkeypatch.setattr(script, "sql_request", lambda *args, **kwargs: {"items": [{"name": "gametrack"}]})
    monkeypatch.setattr(script, "add_version", lambda *args: pytest.fail("Existing secrets were replaced"))
    assert script.main(["--project", "example-project", "--connection-name",
                        "example-project:us-central1:gametrack-postgres"]) == 0
    assert "fake-token" not in capsys.readouterr().out


def test_settings_preserves_stages_and_rejects_other_project(tmp_path, monkeypatch):
    script = load_script("settings")
    monkeypatch.setattr(script, "__file__", str(tmp_path / "settings.py"))
    script.main(["--project", "example-project", "--enable-jobs"])
    script.main(["--training-version", "demo-v1"])
    import json
    values = json.loads((tmp_path / "settings.auto.tfvars.json").read_text())
    assert values["deploy_jobs"] is True
    assert "deploy_web" not in values
    assert "model_version" not in values
    assert values["training_version"] == "demo-v1"
    with pytest.raises(SystemExit):
        script.main(["--project", "another-project"])
    with pytest.raises(SystemExit):
        script.main(["--enable-scheduler"])


def test_secret_saved_before_sql_creation_and_not_printed(monkeypatch, capsys):
    script = load_script("configure_secrets")
    operations = []
    monkeypatch.setattr(script, "has_version", lambda project, name: name != "gametrack-database-url")
    monkeypatch.setattr(script, "gcloud", lambda *args, **kwargs: "fake-token")
    monkeypatch.setattr(script, "add_version", lambda project, name, value: operations.append(("secret", value)))
    monkeypatch.setattr(script, "wait_operation", lambda *args: None)

    def sql_request(project, token, path, body=None):
        if body is None:
            return {"items": []}
        operations.append(("sql", body["password"]))
        return {"name": "fake-operation"}

    monkeypatch.setattr(script, "sql_request", sql_request)
    assert script.main(["--project", "example-project", "--connection-name",
                        "example-project:us-central1:gametrack-postgres"]) == 0
    assert [operation[0] for operation in operations] == ["secret", "sql"]
    assert operations[1][1] in operations[0][1]
    assert operations[1][1] not in capsys.readouterr().out


def test_skip_steam_never_reads_or_exports_existing_key(monkeypatch):
    script = load_script("configure_secrets")

    def has_version(project, name):
        assert name != "gametrack-steam-key"
        return True

    monkeypatch.setattr(script, "has_version", has_version)
    monkeypatch.setattr(script, "read_steam_key", lambda *args: pytest.fail("Steam key was read"))
    monkeypatch.setattr(script, "add_version", lambda *args: pytest.fail("A secret was exported"))
    monkeypatch.setattr(script, "gcloud", lambda *args, **kwargs: "fake-token")
    monkeypatch.setattr(script, "sql_request", lambda *args, **kwargs: {"items": [{"name": "gametrack"}]})
    assert script.main(["--project", "example-project", "--connection-name",
                        "example-project:us-central1:gametrack-postgres", "--skip-steam"]) == 0
