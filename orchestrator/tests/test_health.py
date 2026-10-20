from pathlib import Path


def test_health_meldet_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    from app import __version__

    assert response.json() == {"status": "ok", "db": "ok", "version": __version__}
    assert __version__ == (Path(__file__).resolve().parents[2] / "VERSION").read_text().strip()
