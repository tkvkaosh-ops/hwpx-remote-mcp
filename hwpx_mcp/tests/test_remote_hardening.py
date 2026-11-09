import zipfile
from urllib.parse import urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lxml import etree

from hwpx_mcp.tools import hwpx_templates
from hwpx_mcp.tools.hwpx_templates import OpenAIFile, _replace_text, _download_template
from hwpx_mcp.tools.remote_documents import (
    build_download_router, create_remote_hwpx_document, validate_hwpx_archive, _public_base_url,
)


def _xml(body):
    return etree.fromstring((
        '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
        'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
        + body + '</hs:sec>'
    ).encode())


def test_split_placeholder_preserves_unrelated_run_styles():
    root = _xml('<hp:p><hp:run charPrIDRef="1"><hp:t>굵은 앞말 {{회</hp:t></hp:run>'
                '<hp:run charPrIDRef="2"><hp:t>사}} 기울임 뒷말</hp:t></hp:run></hp:p>')
    assert _replace_text(root, {'{{회사}}': '매실한입'}) == ['{{회사}}']
    texts = root.xpath('//*[local-name()="t"]/text()')
    assert texts == ['굵은 앞말 매실한입', ' 기울임 뒷말']
    assert root.xpath('//*[local-name()="run"]/@charPrIDRef') == ['1', '2']


def test_replacements_do_not_join_different_table_cells():
    root = _xml('<hp:p><hp:run><hp:tbl><hp:tr>'
                '<hp:tc><hp:p><hp:run><hp:t>{{회</hp:t></hp:run></hp:p></hp:tc>'
                '<hp:tc><hp:p><hp:run><hp:t>사}}</hp:t></hp:run></hp:p></hp:tc>'
                '</hp:tr></hp:tbl></hp:run></hp:p>')
    before = etree.tostring(root)
    assert _replace_text(root, {'{{회사}}': '매실한입'}) == []
    assert etree.tostring(root) == before


def test_multiline_text_and_xml_characters():
    root = _xml('<hp:p><hp:run><hp:t>{{회사}}<hp:lineBreak/>{{회사}}</hp:t></hp:run></hp:p>')
    _replace_text(root, {'{{회사}}': '매실 & 한입 <확인>'})
    assert list(root.itertext()) == ['매실 & 한입 <확인>', '매실 & 한입 <확인>']
    assert len(root.xpath('//*[local-name()="lineBreak"]')) == 1


def test_empty_replacement_is_rejected():
    with pytest.raises(ValueError, match='must not be empty'):
        _replace_text(_xml('<hp:p><hp:run><hp:t>hello</hp:t></hp:run></hp:p>'), {'': 'x'})


def test_archive_size_limit_is_checked_before_decompression(tmp_path, monkeypatch):
    path = tmp_path / 'large.hwpx'
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('big.xml', b'0' * 2000)
    monkeypatch.setenv('HWPX_MAX_UNCOMPRESSED_BYTES', '1000')
    monkeypatch.setattr(zipfile.ZipFile, 'testzip', lambda self: pytest.fail('decompressed before size check'))
    result = validate_hwpx_archive(path)
    assert not result['valid']
    assert 'archive uncompressed size exceeds limit' in result['errors']


def test_expiry_parameter_cannot_delete_unexpired_file(tmp_path, monkeypatch):
    monkeypatch.setenv('HWPX_OUTPUT_DIR', str(tmp_path))
    monkeypatch.setenv('PUBLIC_BASE_URL', 'https://documents.example.test')
    result = create_remote_hwpx_document('report.hwpx', '보존 시험')
    path = urlparse(result['download_url']).path
    app = FastAPI()
    app.include_router(build_download_router())
    with TestClient(app) as client:
        assert client.get(path + '?expires=0&signature=bad').status_code == 410
        assert client.get(result['download_url']).status_code == 200
    assert len(list(tmp_path.glob('*/report.hwpx'))) == 1


def test_openai_file_properties_are_optional_strings_not_nullable():
    schema = OpenAIFile.model_json_schema()
    assert schema['properties']['mime_type']['type'] == 'string'
    assert schema['properties']['file_name']['type'] == 'string'
    assert schema['required'] == ['download_url', 'file_id']


def test_dns_rebinding_is_blocked_before_connection(monkeypatch):
    calls = 0
    def resolve(*args, **kwargs):
        nonlocal calls
        calls += 1
        return [(2, 1, 6, '', ('93.184.216.34' if calls == 1 else '127.0.0.1', 443))]
    monkeypatch.delenv('ALLOW_PRIVATE_DOWNLOADS', raising=False)
    monkeypatch.setattr(hwpx_templates.socket, 'getaddrinfo', resolve)
    import httpx
    with hwpx_templates._PinnedDownloadTransport() as transport:
        with pytest.raises(ValueError, match='non-public'):
            transport.handle_request(httpx.Request('GET', 'https://template.example/file.hwpx'))


def test_own_signed_template_works_without_private_network_exception(tmp_path, monkeypatch):
    monkeypatch.setenv('HWPX_OUTPUT_DIR', str(tmp_path / 'published'))
    monkeypatch.setenv('PUBLIC_BASE_URL', 'http://127.0.0.1:8790')
    monkeypatch.delenv('ALLOW_PRIVATE_DOWNLOADS', raising=False)
    result = create_remote_hwpx_document('template.hwpx', '{{회사}}')
    destination = tmp_path / 'downloaded.hwpx'
    file_info = OpenAIFile(download_url=result['download_url'], file_id='upload_test')
    assert _download_template(file_info, destination)['valid']
    assert destination.is_file()
    bad = file_info.model_copy(update={'download_url': result['download_url'] + 'bad'})
    with pytest.raises(ValueError, match='signature'):
        _download_template(bad, tmp_path / 'should-not-exist.hwpx')


def test_unsigned_private_url_is_rejected(monkeypatch):
    monkeypatch.delenv('ALLOW_PRIVATE_DOWNLOADS', raising=False)
    with pytest.raises(ValueError, match='HTTPS'):
        hwpx_templates._validate_remote_url('http://127.0.0.1/private')


def test_render_url_and_upload_limit_are_used_without_manual_base_url(monkeypatch):
    monkeypatch.delenv('PUBLIC_BASE_URL', raising=False)
    monkeypatch.setenv('RENDER_EXTERNAL_URL', 'https://example.onrender.com')
    monkeypatch.setenv('HWPX_MAX_UPLOAD_BYTES', str(10 * 1048576))
    assert _public_base_url() == 'https://example.onrender.com'
    app = FastAPI()
    app.include_router(build_download_router())
    with TestClient(app) as client:
        assert '최대 10MB' in client.get('/upload').text
