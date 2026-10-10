"""Comprueba el despliegue sin mostrar tokens ni datos personales."""
import argparse
import json
from urllib.parse import parse_qs, urlparse

import httpx
import truststore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--demo", action="store_true", help="Probar las cuentas demo existentes")
    args = parser.parse_args()
    origin = args.url.rstrip("/")
    parsed = urlparse(origin)
    if parsed.scheme != "https" or not parsed.hostname or parsed.path:
        parser.error("Usá el origen HTTPS del despliegue.")
    truststore.inject_into_ssl()
    report = {}
    with httpx.Client(base_url=origin, timeout=300) as client:
        for path in ("/", "/api", "/api/v1/health", "/api/v1/games?limit=2"):
            response = client.get(path)
            response.raise_for_status()
            report[path] = response.status_code
            if path == "/api/v1/health":
                assert response.json()["database"] == "ok"
        status = client.get("/api/v1/steam/catalog/status")
        status.raise_for_status()
        report["steam"] = {key: status.json().get(key) for key in
                           ("key_configured", "worker_mode", "worker_running")}
        assert report["steam"]["key_configured"] is True
        start = client.get("/api/v1/auth/steam/start", follow_redirects=False)
        assert start.status_code in (302, 303, 307)
        redirect = urlparse(start.headers["location"])
        assert redirect.scheme == "https" and redirect.hostname == "steamcommunity.com"
        assert parse_qs(redirect.query)["openid.return_to"][0].startswith(origin + "/")
        report["steam_https_callback"] = True
        unauthorized = client.get("/api/v1/analytics/overview")
        assert unauthorized.status_code == 401
        report["private_access"] = unauthorized.status_code
        if args.demo:
            for username in ("jugador.demo", "nuevo.demo", "dev.demo"):
                login = client.post("/api/v1/auth/login", json={"username": username, "password": "demo1234"})
                login.raise_for_status()
                token = login.json()["access_token"]
                headers = {"Authorization": "Bearer " + token}
                check = {"login": login.status_code, "role": login.json()["user"]["role"]}
                paths = ("/api/v1/auth/me", "/api/v1/analytics/overview", "/api/v1/analytics/reviews?limit=2") if username == "dev.demo" else (
                    "/api/v1/auth/me", "/api/v1/friends", "/api/v1/recommendations?limit=3")
                for path in paths:
                    response = client.get(path, headers=headers)
                    response.raise_for_status()
                    check[path] = response.status_code
                    if "/recommendations" in path:
                        data = response.json()
                        assert data["items"]
                        check["recommendations"] = len(data["items"])
                        check["local_model"] = data["local_model"]
                        # Nuevas notas pueden dejar el artefacto como "stale":
                        # sigue siendo compatible y usable sin reentrenarlo.
                        assert data["local_model"]["status"] in {"ready", "stale"}
                if username == "jugador.demo":
                    manual = client.get("/api/v1/recommendations?limit=3&strategy=ia_local", headers=headers)
                    manual.raise_for_status()
                    assert manual.json()["effective_strategy"] == "ia_local"
                    check["manual_model"] = manual.status_code
                if username != "dev.demo":
                    forbidden = client.get("/api/v1/analytics/overview", headers=headers)
                    assert forbidden.status_code == 403
                    check["developer_access"] = forbidden.status_code
                report[username] = check
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
