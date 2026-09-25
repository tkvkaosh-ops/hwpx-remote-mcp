import zipfile
from pathlib import Path
from urllib.parse import urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hwpx_mcp.tools.remote_documents import (
    HWPX_MIME_TYPE,
    build_download_router,
    create_remote_hwpx_document,
    normalize_filename,
    validate_hwpx_archive,
)


def _create_test_document(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setenv("HWPX_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://documents.example.test")
    return create_remote_hwpx_document(
        filename="test_report.hwpx",
        title="AI 활용 교육 결과보고서",
        sections=[
            {"heading": "교육개요", "content": "생성형 AI 활용 교육을 실시하였다."},
            {"heading": "교육내용", "items": ["기본 원리", "문서 작성 실습"]},
            {"heading": "교육성과", "content": "업무 활용 역량이 향상되었다."},
            {"heading": "향후계획", "content": "심화 교육을 운영한다."},
        ],
        tables=[
            {
                "title": "교육 운영 요약",
                "headers": ["구분", "내용"],
                "rows": [["교육방식", "이론 및 실습"]],
                "style": "thin",
            }
        ],
    )


def test_create_remote_hwpx_document_is_valid_and_downloadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    result = _create_test_document(tmp_path, monkeypatch)

    assert result["success"] is True
    assert result["filename"] == "test_report.hwpx"
    assert result["mime_type"] == HWPX_MIME_TYPE
    assert result["validation"]["valid"] is True
    assert result["download_url"].startswith(
        "https://documents.example.test/downloads/"
    )

    parsed_url = urlparse(result["download_url"])
    url_path = parsed_url.path
    _, _, document_id, filename = url_path.split("/", 3)
    file_path = tmp_path / document_id / filename
    assert file_path.is_file()
    assert validate_hwpx_archive(file_path)["valid"] is True
    with zipfile.ZipFile(file_path, "r") as archive:
        section_xml = archive.read("Contents/section0.xml").decode("utf-8")
        assert 'charPrIDRef="201"' in section_xml
        assert 'charPrIDRef="202"' in section_xml
        assert 'borderFillIDRef="102"' in section_xml
        assert archive.getinfo("mimetype").compress_type == zipfile.ZIP_STORED

    app = FastAPI()
    app.include_router(build_download_router())
    with TestClient(app) as client:
        response = client.get(f"{url_path}?{parsed_url.query}")

    assert response.status_code == 200
    assert response.headers["content-type"] == HWPX_MIME_TYPE
    assert response.content == file_path.read_bytes()


@pytest.mark.parametrize(
    "filename",
    ["../escape.hwpx", "folder/document.hwpx", "folder\\document.hwpx", "bad\x00.hwpx"],
)
def test_normalize_filename_rejects_paths_and_control_chars(filename: str):
    with pytest.raises(ValueError):
        normalize_filename(filename)


def test_download_route_returns_404_for_unknown_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("HWPX_OUTPUT_DIR", str(tmp_path))
    app = FastAPI()
    app.include_router(build_download_router())

    with TestClient(app) as client:
        response = client.get(
            f"/downloads/{'0' * 32}/missing.hwpx?expires=0&signature=bad"
        )

    assert response.status_code == 410
