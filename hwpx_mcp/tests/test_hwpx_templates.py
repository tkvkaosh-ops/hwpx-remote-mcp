import hashlib
import threading
import zipfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest
from lxml import etree

from hwpx_mcp.tools.hwpx_builder import HwpxBuilder
from hwpx_mcp.tools.hwpx_templates import (
    OpenAIFile,
    _replace_field_values,
    fill_remote_hwpx_template,
    inspect_remote_hwpx_template,
)
from hwpx_mcp.tools.remote_documents import validate_hwpx_archive


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        return


@pytest.fixture
def template_server(tmp_path: Path):
    template_path = tmp_path / "template.hwpx"
    builder = HwpxBuilder()
    builder.add_heading("{{교육명}} 결과보고서", level=1)
    builder.add_heading("교육개요", level=2)
    builder.add_text("담당자: {{담당자}}")
    builder.add_table(
        2,
        2,
        [["교육대상", "기존 대상"], ["교육일시", "기존 일시"]],
        style="thin",
    )
    builder.save(str(template_path))

    handler = partial(_QuietHandler, directory=str(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield template_path, f"http://127.0.0.1:{server.server_port}/template.hwpx"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _file_input(url: str) -> OpenAIFile:
    return OpenAIFile(
        download_url=url,
        file_id="file_template_test",
        mime_type="application/vnd.hancom.hwpx",
        file_name="template.hwpx",
    )


def test_inspect_and_fill_template_preserves_unmodified_package_parts(
    template_server,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    template_path, url = template_server
    output_dir = tmp_path / "results"
    monkeypatch.setenv("ALLOW_PRIVATE_DOWNLOADS", "true")
    monkeypatch.setenv("HWPX_OUTPUT_DIR", str(output_dir))
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://files.example.test")
    monkeypatch.setenv("DOWNLOAD_SIGNING_KEY", "test-signing-key")

    source_hash = hashlib.sha256(template_path.read_bytes()).hexdigest()
    inspection = inspect_remote_hwpx_template(_file_input(url))

    assert inspection["success"] is True
    assert set(inspection["placeholders"]) == {"교육명", "담당자"}
    assert inspection["section_count"] == 1
    assert inspection["tables"][0]["row_count"] == 2
    assert "교육대상" in inspection["detected_labels"]

    result = fill_remote_hwpx_template(
        template_file=_file_input(url),
        filename="교육결과보고서_완성본.hwpx",
        replacements={"{{교육명}}": "AI 활용 교육", "{{담당자}}": "홍길동"},
        table_cell_values=[{"label": "교육대상", "value": "전 직원"}],
        paragraphs=[{"after_text": "교육개요", "content": "생성형 AI 실무 교육"}],
    )

    assert result["success"] is True
    assert result["original_modified"] is False
    assert result["expires_at"]
    assert hashlib.sha256(template_path.read_bytes()).hexdigest() == source_hash

    parsed = urlparse(result["download_url"])
    document_id = parsed.path.split("/")[2]
    output_path = output_dir / document_id / "교육결과보고서_완성본.hwpx"
    assert validate_hwpx_archive(output_path)["valid"] is True

    with zipfile.ZipFile(template_path, "r") as source, zipfile.ZipFile(
        output_path, "r"
    ) as completed:
        for name in source.namelist():
            if name not in {"Contents/section0.xml", "Preview/PrvText.txt"}:
                assert completed.read(name) == source.read(name)

        section_xml = completed.read("Contents/section0.xml").decode("utf-8")
        assert "AI 활용 교육 결과보고서" in section_xml
        assert "홍길동" in section_xml
        assert "전 직원" in section_xml
        assert "생성형 AI 실무 교육" in section_xml
        assert "기존 대상" not in section_xml


def test_native_field_replacement_preserves_field_markup():
    root = etree.fromstring(
        b'<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
        b'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
        b'<hp:p><hp:run><hp:fieldBegin name="student"/><hp:t>old</hp:t>'
        b'<hp:fieldEnd/></hp:run></hp:p></hs:sec>'
    )

    applied = _replace_field_values(root, {"student": "김학생"})

    assert applied == ["student"]
    assert root.xpath("string(.//*[local-name()='t'])") == "김학생"
    assert len(root.xpath(".//*[local-name()='fieldBegin']")) == 1
    assert len(root.xpath(".//*[local-name()='fieldEnd']")) == 1


def test_file_parameter_schema_has_only_required_openai_fields():
    schema = OpenAIFile.model_json_schema()

    assert set(schema["properties"]) == {
        "download_url",
        "file_id",
        "mime_type",
        "file_name",
    }
    assert schema["required"] == ["download_url", "file_id"]
    assert schema["additionalProperties"] is False
