"""POST /v1/thumbnail and GET /health."""

from __future__ import annotations

from fastapi.testclient import TestClient

from webpthumb.api import create_app

from samples import heif_bytes, pdf_bytes, png_bytes


def test_health() -> None:
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_png_upload_returns_webp() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/v1/thumbnail",
        files={"file": ("wide.png", png_bytes((400, 100)), "image/png")},
        params={"width": 32, "quality": 70},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/webp")
    assert 'filename="wide.webp"' in response.headers["content-disposition"]
    assert response.content[:4] == b"RIFF"
    assert response.content[8:12] == b"WEBP"


def test_heif_upload_returns_webp() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/v1/thumbnail",
        files={"file": ("photo.heif", heif_bytes((400, 100)), "image/heif")},
        params={"width": 32, "quality": 70},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/webp")
    assert 'filename="photo.webp"' in response.headers["content-disposition"]
    assert response.content[:4] == b"RIFF"
    assert response.content[8:12] == b"WEBP"


def test_pdf_upload_returns_webp() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/v1/thumbnail",
        files={"file": ("doc.pdf", pdf_bytes(), "application/pdf")},
        params={"width": 48, "page": 1},
    )
    assert response.status_code == 200
    assert response.content[8:12] == b"WEBP"


def test_text_upload_is_400() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/v1/thumbnail",
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 400


def test_upload_over_limit_is_413() -> None:
    client = TestClient(create_app(max_upload_bytes=8))
    response = client.post(
        "/v1/thumbnail",
        files={"file": ("wide.png", b"0123456789", "image/png")},
    )
    assert response.status_code == 413


def test_bad_page_is_400() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/v1/thumbnail",
        files={"file": ("doc.pdf", pdf_bytes(), "application/pdf")},
        params={"page": 9},
    )
    assert response.status_code == 400
