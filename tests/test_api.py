"""FastAPI 接口层冒烟测试。"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_examples_load():
    r = client.get("/api/examples")
    assert r.status_code == 200
    ids = {e["id"] for e in r.json()}
    assert {"redirect-https", "loop-detected", "syntax-errors"} <= ids


def test_debug_redirect_example():
    payload = {
        "path": "/old/page/",
        "max_steps": 50,
        "rules": "^(/.*?)/+$  $1\n^/old/(.*)$  /archive/$1  permanent\n",
    }
    r = client.post("/api/debug", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["status_code"] == 301
    assert body["final_url"] == "/archive/page"
    assert body["steps"]


def test_debug_syntax_error_returns_422_locations():
    r = client.post(
        "/api/debug",
        json={"path": "/x", "rules": "^/blog/([a-z]+$ /n\n^/a$ /$2\n"},
    )
    assert r.status_code == 422
    body = r.json()
    assert body["error"] == "rule_syntax"
    lines = {e["line"] for e in body["errors"]}
    assert lines == {1, 2}
    assert all(e["column"] >= 1 for e in body["errors"])


def test_debug_empty_path_rejected():
    r = client.post("/api/debug", json={"path": "   ", "rules": "^/a$ /b"})
    assert r.status_code == 400


def test_debug_loop_example_508():
    r = client.post(
        "/api/debug",
        json={"path": "/a", "rules": "^/a$ /b last\n^/b$ /a last\n"},
    )
    body = r.json()
    assert body["status_code"] == 508
    assert body["loop_detected"] is True


def test_index_served():
    r = client.get("/")
    assert r.status_code == 200
    assert "URL 重写规则调试台" in r.text


def test_static_assets_served():
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_leading_slash_auto_added():
    r = client.post("/api/debug", json={"path": "old/x", "rules": "old new"})
    body = r.json()
    assert body["initial_url"] == "/old/x"
    assert body["final_url"] == "/new/x"
