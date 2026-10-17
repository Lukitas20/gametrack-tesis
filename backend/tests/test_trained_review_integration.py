"""Integración en una base efímera: los fixtures nunca son datos de entrenamiento."""
import pytest
from sqlalchemy import select

from tests.test_api import auth, client, data, db
from app.models import Review, User
from app.services import trained_ai_service as service


class InferenceSpy:
    def __init__(self):
        self.texts = []
        self.calls = []
        self.evidence_calls = 0

    def predict(self, texts):
        self.texts.extend(texts)
        self.calls.append(len(texts))
        return [dict(reception=.1 if "rechazo" in text else .9,
                     topics=[dict(topic="optimizacion", strength=.8)]) for text in texts]

    def evidence(self, text, topic):
        self.evidence_calls += 1
        return text


@pytest.fixture
def model(monkeypatch):
    predictor = InferenceSpy()
    status = dict(status="ready", source="real_steam_reviews", examples=24000, games=2687,
                  topics={topic: dict(eligible=topic == "optimizacion") for topic in service.TOPICS})
    monkeypatch.setattr(service, "load_model", lambda: (predictor, status))
    return predictor


@pytest.fixture
def sample(db, data):
    data["breve"].developer = "Estudio Test"
    data["largo"].developer = " ESTUDIO TEST "
    data["incognito"].developer = "Otro estudio"
    fake_user = User(username="legacy-integration", bio="Perfil de gusto: no usar")
    db.add(fake_user)
    db.flush()
    real = [Review(game_id=data["breve"].id, source="steam", language="es",
                   content=f"Rendimiento: rechazo observado {index}.", is_recommended=False)
            for index in range(5)]
    real.append(Review(game_id=data["breve"].id, user_id=data["player"].id,
                       source="user", content="Rendimiento: recomendación del usuario real.",
                       is_recommended=True))
    real.append(Review(game_id=data["breve"].id, source="steam", language="es",
                       content="Rendimiento sin voto de recomendación.", is_recommended=None))
    same_studio = Review(game_id=data["largo"].id, source="steam", content="Rendimiento en otro título propio.")
    excluded = [
        Review(game_id=data["breve"].id, source="seed", content="No analizar seed."),
        Review(game_id=data["breve"].id, source="user", content="No analizar sin autor."),
        Review(game_id=data["breve"].id, source="user", user_id=fake_user.id, content="No analizar perfil demo."),
        Review(game_id=data["breve"].id, source="steam", language="en", content="No analizar otro idioma."),
        Review(game_id=data["breve"].id, source="steam", content="  "),
        Review(game_id=data["incognito"].id, source="steam", content="No analizar estudio ajeno."),
    ]
    db.add_all(real + [same_studio] + excluded)
    db.commit()
    return real


def test_real_model_report_keeps_votes_and_sources_separate(db, data, sample, model):
    report = service.review_model_report(db, game_id=data["breve"].id)
    assert report["sampled"] == report["total"] == 7
    topic = report["topics"][0]
    assert topic["mentions"] == 7
    assert (topic["not_recommended"], topic["recommended"], topic["without_recommendation"]) == (5, 1, 1)
    assert len(topic["examples"]) == 3
    assert all(example["recommended"] is False for example in topic["examples"])
    assert {example["review_id"] for example in topic["examples"]} <= {review.id for review in sample}
    assert all(example["quote"] in model.texts for example in topic["examples"])
    assert not any("No analizar" in text for text in model.texts)
    # Inferir no modifica el análisis por reglas ni la recomendación del autor.
    assert all(review.is_analyzed is False for review in db.scalars(select(Review)))
    assert sample[-2].is_recommended is True
    assert report["reception"] == dict(compared=6, disagreements=0)


def test_model_report_is_bounded_batched_and_studio_scoped(db, data, sample, model, monkeypatch):
    monkeypatch.setattr(service, "MAX_REVIEWS", 3)
    monkeypatch.setattr(service, "INFERENCE_BATCH", 2)
    report = service.review_model_report(db, studio=" estudio test ", limit=999999)
    assert report["total"] == 8 and report["sampled"] == 3
    assert model.calls == [2, 1]
    assert report["scope"] == dict(type="studio", studio="estudio test")


def test_missing_model_keeps_actual_coverage_without_inference(db, data, sample, monkeypatch):
    monkeypatch.setattr(service, "load_model", lambda: (None, dict(status="missing", message="No disponible")))
    report = service.review_model_report(db, game_id=data["breve"].id)
    assert report["model"]["status"] == "missing"
    assert report["total"] == 7 and report["sampled"] == 0 and report["topics"] == []


def test_model_inference_failure_preserves_other_report_sections(client, data, sample, model, monkeypatch):
    monkeypatch.setattr(model, "predict", lambda texts: [])
    response = client.get("/api/v1/analytics/studio", headers=auth(client, "dev"))
    assert response.status_code == 200
    report = response.json()
    assert len(report["juegos"]) == 2
    assert report["trained_ai"]["model"]["status"] == "invalid"
    assert report["trained_ai"]["sampled"] == 0


def test_evidence_failure_uses_original_text_without_inventing_quote(db, data, sample, model, monkeypatch):
    def unavailable(*args):
        raise ValueError("No fragmento")
    monkeypatch.setattr(model, "evidence", unavailable)
    report = service.review_model_report(db, game_id=data["breve"].id)
    assert report["model"]["status"] == "ready"
    assert all(item["quote"] in model.texts for item in report["topics"][0]["examples"])


def test_api_integrates_model_in_existing_reports_and_assistant(client, db, data, sample, model, monkeypatch):
    monkeypatch.setattr("app.services.steam_service.maybe_refresh", lambda db, game: True)
    headers = auth(client, "dev")
    response = client.get(f"/api/v1/analytics/games/{data['breve'].id}", headers=headers)
    assert response.status_code == 200
    report = response.json()
    assert report["trained_ai"]["sampled"] == 7
    assert report["trained_ai"]["topics"][0]["not_recommended"] == 5
    studio = client.get("/api/v1/analytics/studio", headers=headers).json()
    assert studio["trained_ai"]["total"] == 8
    answer = client.post("/api/v1/analytics/assistant", headers=headers, json={
        "game_id": data["breve"].id, "question": "¿Qué temas detecta la IA en estas reseñas?"
    }).json()
    assert answer["trained_ai_used"] is True and answer["model_status"] == "ready"
    assert "7 reseñas reales" in answer["answer"]
    assert "rendimiento y errores: 7 textos" in answer["answer"]
    assert "no indican" in answer["answer"] and len(answer["evidence"]) == 1
    assert answer["evidence"][0]["review_id"] in {review.id for review in sample}
    priority = client.post("/api/v1/analytics/assistant", headers=headers, json={
        "game_id": data["breve"].id, "question": "¿Qué revisar de rendimiento?"
    }).json()
    assert "ni demuestra por qué" in priority["answer"]
    assert len(priority["evidence"]) == 3


def test_review_model_status_is_developer_only(client, data):
    url = "/api/v1/analytics/review-model"
    assert client.get(url).status_code == 401
    assert client.get(url, headers=auth(client, "jugadora")).status_code == 403
    response = client.get(url, headers=auth(client, "dev"))
    assert response.status_code == 200
    assert response.json()["status"] in {"ready", "missing", "invalid"}
    assert response.headers["cache-control"] == "no-store"


def test_evidence_extraction_stops_after_three_negative_examples(db, data, model):
    db.add_all([Review(game_id=data["breve"].id, source="steam", content=f"rechazo rendimiento {index}",
                       is_recommended=False) for index in range(30)])
    db.commit()
    report = service.review_model_report(db, game_id=data["breve"].id)
    assert report["topics"][0]["mentions"] == 30
    assert model.evidence_calls == 3


def test_unspecified_scope_never_uses_entire_catalog(db, model):
    report = service.review_model_report(db)
    assert report["scope"]["type"] == "unspecified"
    assert report["sampled"] == 0 and report["total"] == 0
    assert model.calls == []


def test_available_model_with_no_predictions_is_not_marked_used(db,data,sample,model,monkeypatch):
    monkeypatch.setattr(model,"predict",lambda texts:[dict(reception=None,topics=[]) for _ in texts])
    report=service.review_model_report(db,game_id=data["breve"].id)
    assert report["model"]["status"]=="ready" and report["sampled"]==7
    assert report["predicted"]==0 and report["used"] is False
    assert service.explain_trained_report(report,"¿Qué temas hay?")["trained_ai_used"] is False
