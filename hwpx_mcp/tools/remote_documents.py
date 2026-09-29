"""Remote-first HWPX creation and download support.

This module keeps the public HTTP download surface separate from the MCP
composition root.  Generated files are stored under an opaque UUID directory
so callers cannot enumerate or overwrite one another's documents.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import hashlib
import hmac
import json
import secrets
import shutil
import tempfile
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import FileResponse, HTMLResponse

from hwpx_mcp.core.xml_parser import SecureXmlParser
from hwpx_mcp.tools.hwpx_builder import BORDER_STYLES, CHAR_STYLES, HwpxBuilder
from hwpx_mcp.tools.hwpx_templates import (
    OpenAIFile,
    fill_remote_hwpx_template,
    inspect_remote_hwpx_template,
)
from hwpx_mcp.tools.hwpx_widget import WIDGET_URI, register_hwpx_widget

logger = logging.getLogger("hwp-mcp-extended.remote_documents")

HWPX_MIME_TYPE = "application/vnd.hancom.hwpx"
REQUIRED_HWPX_PARTS = {
    "mimetype",
    "version.xml",
    "Contents/content.hpf",
    "Contents/header.xml",
    "Contents/section0.xml",
}
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_EPHEMERAL_SIGNING_KEY = secrets.token_bytes(32)
_UPLOAD_MIME_TYPES = {
    HWPX_MIME_TYPE,
    "application/hwp+zip",
    "application/zip",
    "application/octet-stream",
}
_UPLOAD_PAGE = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>HWPX 서식 업로드</title>
  <style>
    :root { color-scheme: light; font-family: Pretendard, "Noto Sans KR", sans-serif; }
    body { margin: 0; background: #f5f7fb; color: #172033; }
    main { max-width: 720px; margin: 8vh auto; padding: 40px; background: white;
      border: 1px solid #dce2ee; border-radius: 22px; box-shadow: 0 18px 60px #25325218; }
    h1 { margin: 0 0 12px; font-size: 30px; }
    p { line-height: 1.65; color: #526078; }
    .drop { display: block; margin: 28px 0 16px; padding: 32px; text-align: center;
      border: 2px dashed #7b8eb8; border-radius: 16px; background: #f8faff; cursor: pointer; }
    input { display: none; }
    button { border: 0; border-radius: 12px; padding: 13px 18px; font-weight: 700;
      color: white; background: #3156d3; cursor: pointer; }
    button:disabled { opacity: .45; cursor: wait; }
    .result { display: none; margin-top: 24px; padding: 20px; border-radius: 14px; background: #eef4ff; }
    textarea { box-sizing: border-box; width: 100%; min-height: 150px; margin: 10px 0;
      padding: 12px; border: 1px solid #bac7df; border-radius: 10px; resize: vertical; }
    .error { color: #b42318; }
    small { color: #6b7890; }
  </style>
</head>
<body><main>
  <h1>Claude용 HWPX 서식 업로드</h1>
  <p>Claude 웹은 HWPX 직접 첨부를 지원하지 않습니다. 여기서 서식을 올린 뒤 생성된 문장을 Claude 채팅에 붙여넣으세요.</p>
  <label class="drop" for="file"><strong id="label">HWPX 파일 선택</strong><br><small>최대 20MB · ZIP/XML 구조 자동검사</small></label>
  <input id="file" type="file" accept=".hwpx,application/vnd.hancom.hwpx">
  <button id="upload" disabled>보안 링크 만들기</button>
  <p id="status"></p>
  <section class="result" id="result">
    <strong>Claude에 붙여넣을 요청문</strong>
    <textarea id="prompt" readonly></textarea>
    <button id="copy">요청문 복사</button>
    <p><a id="download" rel="noopener">업로드한 원본 확인</a></p>
  </section>
</main>
<script>
const file = document.querySelector('#file');
const upload = document.querySelector('#upload');
const label = document.querySelector('#label');
const status = document.querySelector('#status');
const result = document.querySelector('#result');
file.addEventListener('change', () => {
  const selected = file.files[0];
  label.textContent = selected ? selected.name : 'HWPX 파일 선택';
  upload.disabled = !selected;
});
upload.addEventListener('click', async () => {
  const selected = file.files[0];
  if (!selected) return;
  upload.disabled = true; status.className = ''; status.textContent = '검사하고 업로드하는 중…';
  result.style.display = 'none';
  try {
    const response = await fetch('/uploads', {
      method: 'POST', body: selected,
      headers: {'Content-Type': selected.type || 'application/octet-stream',
                'X-File-Name': encodeURIComponent(selected.name)}
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || '업로드 실패');
    document.querySelector('#prompt').value = data.claude_prompt;
    document.querySelector('#download').href = data.download_url;
    result.style.display = 'block'; status.textContent = '완료';
  } catch (error) {
    status.className = 'error'; status.textContent = error.message;
  } finally { upload.disabled = false; }
});
document.querySelector('#copy').addEventListener('click', async () => {
  await navigator.clipboard.writeText(document.querySelector('#prompt').value);
  document.querySelector('#copy').textContent = '복사됨';
});
</script></body></html>
"""


def get_output_dir() -> Path:
    """Return the configured persistent output directory."""
    return Path(os.getenv("HWPX_OUTPUT_DIR", "/app/output")).expanduser().resolve()


def normalize_filename(filename: str) -> str:
    """Validate a user-visible filename without permitting path traversal."""
    value = filename.strip()
    if not value:
        value = "document.hwpx"
    if not value.lower().endswith(".hwpx"):
        value += ".hwpx"
    if value in {".", ".."} or "/" in value or "\\" in value or Path(value).name != value:
        raise ValueError("filename must be a plain file name, not a path")
    if _CONTROL_CHARS.search(value):
        raise ValueError("filename contains control characters")
    if len(value.encode("utf-8")) > 180:
        raise ValueError("filename is too long")
    return value


def _public_base_url() -> str:
    configured = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured

    railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip().rstrip("/")
    if railway_domain:
        if not railway_domain.startswith(("http://", "https://")):
            railway_domain = f"https://{railway_domain}"
        return railway_domain

    port = os.getenv("PORT", os.getenv("MCP_PORT", "8000"))
    return f"http://localhost:{port}"


def _signing_key() -> bytes:
    configured = os.getenv("DOWNLOAD_SIGNING_KEY", "")
    return configured.encode("utf-8") if configured else _EPHEMERAL_SIGNING_KEY


def _download_ttl_seconds() -> int:
    return max(60, int(os.getenv("HWPX_DOWNLOAD_TTL_SECONDS", "86400")))


def _max_upload_bytes() -> int:
    return max(1024, int(os.getenv("HWPX_MAX_UPLOAD_BYTES", "20971520")))


def _cleanup_expired_artifacts() -> None:
    output_dir = get_output_dir()
    if not output_dir.is_dir():
        return
    now = int(time.time())
    for candidate in output_dir.iterdir():
        if not candidate.is_dir() or not re.fullmatch(r"[0-9a-f]{32}", candidate.name):
            continue
        metadata_path = candidate / "metadata.json"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            expired = int(metadata.get("expires", 0)) < now
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            expired = False
        if expired and candidate.resolve().parent == output_dir:
            shutil.rmtree(candidate, ignore_errors=True)


def _signature(document_id: str, filename: str, expires: int) -> str:
    message = f"{document_id}:{filename}:{expires}".encode("utf-8")
    return hmac.new(_signing_key(), message, hashlib.sha256).hexdigest()


def _result_metadata(
    file_path: Path,
    document_id: str,
    filename: str,
    validation: dict[str, Any],
) -> dict[str, Any]:
    expires = int(time.time()) + _download_ttl_seconds()
    signature = _signature(document_id, filename, expires)
    encoded_filename = quote(filename)
    download_url = (
        f"{_public_base_url()}/downloads/{document_id}/{encoded_filename}"
        f"?expires={expires}&signature={signature}"
    )
    expires_at = datetime.fromtimestamp(expires, timezone.utc).isoformat()
    metadata_path = file_path.parent / "metadata.json"
    metadata_path.write_text(
        json.dumps(
            {"filename": filename, "expires": expires},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    size = file_path.stat().st_size
    return {
        "success": True,
        "filename": filename,
        "mime_type": HWPX_MIME_TYPE,
        "file_size": size,
        "size_bytes": size,
        "download_url": download_url,
        "expires_at": expires_at,
        "validation": validation,
    }


def publish_hwpx_file(source_path: Path, filename: str) -> dict[str, Any]:
    """Copy a completed HWPX into the result store and issue a signed URL."""
    _cleanup_expired_artifacts()
    safe_filename = normalize_filename(filename)
    validation = validate_hwpx_archive(source_path)
    if not validation.get("valid"):
        raise ValueError(
            "HWPX failed validation: " + "; ".join(validation.get("errors", []))
        )

    document_id = uuid.uuid4().hex
    document_dir = get_output_dir() / document_id
    document_dir.mkdir(parents=True, exist_ok=False)
    destination = document_dir / safe_filename
    try:
        shutil.copyfile(source_path, destination)
        return _result_metadata(
            destination, document_id, safe_filename, validation
        )
    except Exception:
        shutil.rmtree(document_dir, ignore_errors=True)
        raise


def _safe_style(value: Any, allowed: dict[str, Any], fallback: str) -> str:
    candidate = str(value or fallback)
    return candidate if candidate in allowed else fallback


def _section_heading(section: dict[str, Any]) -> str:
    return str(
        section.get("heading")
        or section.get("title")
        or section.get("name")
        or ""
    ).strip()


def _paragraphs(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split("\n") if part.strip()]
    if isinstance(value, list):
        return [str(part).strip() for part in value if str(part).strip()]
    return [str(value).strip()] if str(value).strip() else []


def validate_hwpx_archive(file_path: Path) -> dict[str, Any]:
    """Validate ZIP integrity, required package parts, and XML well-formedness."""
    result: dict[str, Any] = {
        "is_zip": False,
        "required_parts_present": False,
        "xml_well_formed": False,
        "checked_xml_files": 0,
        "errors": [],
    }
    try:
        if not zipfile.is_zipfile(file_path):
            result["errors"].append("not a ZIP archive")
            return result

        result["is_zip"] = True
        with zipfile.ZipFile(file_path, "r") as archive:
            corrupt_member = archive.testzip()
            if corrupt_member:
                result["errors"].append(f"corrupt ZIP member: {corrupt_member}")

            members = set(archive.namelist())
            infos = archive.infolist()
            unsafe_members = [
                info.filename
                for info in infos
                if info.filename.startswith(("/", "\\"))
                or ".." in Path(info.filename.replace("\\", "/")).parts
            ]
            if unsafe_members:
                result["errors"].append("unsafe archive member paths detected")

            max_uncompressed = int(
                os.getenv("HWPX_MAX_UNCOMPRESSED_BYTES", "104857600")
            )
            total_uncompressed = sum(info.file_size for info in infos)
            result["uncompressed_size_bytes"] = total_uncompressed
            if total_uncompressed > max_uncompressed:
                result["errors"].append("archive uncompressed size exceeds limit")

            missing = sorted(REQUIRED_HWPX_PARTS - members)
            result["required_parts_present"] = not missing
            if missing:
                result["errors"].append(f"missing required parts: {', '.join(missing)}")

            mimetype = archive.read("mimetype").decode("ascii", errors="replace") if "mimetype" in members else ""
            result["package_mimetype"] = mimetype
            if mimetype != "application/hwp+zip":
                result["errors"].append(f"unexpected package mimetype: {mimetype}")
            if infos and infos[0].filename != "mimetype":
                result["errors"].append("mimetype must be the first archive member")
            if "mimetype" in members and archive.getinfo("mimetype").compress_type != zipfile.ZIP_STORED:
                result["errors"].append("mimetype must be stored without compression")

            xml_members = sorted(
                member
                for member in members
                if member.lower().endswith((".xml", ".hpf"))
            )
            for member in xml_members:
                SecureXmlParser.parse_string(archive.read(member))
            result["checked_xml_files"] = len(xml_members)
            result["xml_well_formed"] = True
    except Exception as exc:
        result["errors"].append(str(exc))

    result["valid"] = not result["errors"]
    return result


def create_remote_hwpx_document(
    filename: str,
    title: str,
    document_content: str = "",
    sections: list[dict[str, Any]] | None = None,
    tables: list[dict[str, Any]] | None = None,
    formatting: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a downloadable HWPX and return JSON-serializable metadata."""
    _cleanup_expired_artifacts()
    safe_filename = normalize_filename(filename)
    sections = sections or []
    tables = tables or []
    formatting = formatting or {}

    title_style = _safe_style(formatting.get("title_style"), CHAR_STYLES, "title")
    body_style = _safe_style(formatting.get("body_style"), CHAR_STYLES, "default")
    section_level = int(formatting.get("section_heading_level", 2))
    if section_level not in (1, 2):
        section_level = 2
    default_table_style = _safe_style(
        formatting.get("table_style"), BORDER_STYLES, "default"
    )

    document_id = uuid.uuid4().hex
    document_dir = get_output_dir() / document_id
    document_dir.mkdir(parents=True, exist_ok=False)
    file_path = document_dir / safe_filename

    try:
        builder = HwpxBuilder()
        if title.strip():
            builder.add_paragraph(title.strip(), style=title_style)

        for paragraph in _paragraphs(document_content):
            builder.add_paragraph(paragraph, style=body_style)

        for section in sections:
            if not isinstance(section, dict):
                raise ValueError("each section must be an object")
            heading = _section_heading(section)
            if heading:
                builder.add_heading(heading, level=section_level)

            section_body = section.get("content", section.get("paragraphs", ""))
            for paragraph in _paragraphs(section_body):
                builder.add_paragraph(paragraph, style=body_style)

            for item in section.get("items", []) or []:
                builder.add_paragraph(f"• {str(item).strip()}", style=body_style)

        for table in tables:
            if not isinstance(table, dict):
                raise ValueError("each table must be an object")
            table_title = str(table.get("title", "")).strip()
            if table_title:
                builder.add_heading(table_title, level=2)

            headers = table.get("headers") or []
            rows = table.get("rows", table.get("data", [])) or []
            if not isinstance(headers, list) or not isinstance(rows, list):
                raise ValueError("table headers and rows must be arrays")
            data = ([headers] if headers else []) + rows
            if not data:
                continue
            normalized_rows = [
                [str(cell) for cell in row]
                if isinstance(row, list)
                else [str(row)]
                for row in data
            ]
            columns = max(len(row) for row in normalized_rows)
            table_style = _safe_style(
                table.get("style"), BORDER_STYLES, default_table_style
            )
            builder.add_table(
                len(normalized_rows), columns, normalized_rows, style=table_style
            )

        builder.save(str(file_path))
        validation = validate_hwpx_archive(file_path)
        if not validation.get("valid"):
            raise ValueError(
                "generated HWPX failed validation: "
                + "; ".join(validation.get("errors", []))
            )

        return _result_metadata(
            file_path, document_id, safe_filename, validation
        )
    except Exception:
        try:
            if file_path.exists():
                file_path.unlink()
            document_dir.rmdir()
        except OSError:
            pass
        raise


def register_remote_document_tools(mcp: Any) -> None:
    """Register the vendor-neutral Remote MCP document tools."""

    from mcp.types import ToolAnnotations

    @mcp.tool(
        title="Create HWPX document",
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    async def create_hwpx_document(
        filename: str,
        title: str,
        document_content: str = "",
        sections: list[dict[str, Any]] | None = None,
        tables: list[dict[str, Any]] | None = None,
        formatting: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a native HWPX document and return a public download URL.

        Use ``sections`` objects with ``heading`` plus ``content`` or
        ``paragraphs``. Use ``tables`` objects with optional ``title``,
        ``headers``, ``rows``, and ``style``. Supported formatting keys are
        ``title_style``, ``body_style``, ``section_heading_level``, and
        ``table_style``.
        """
        try:
            return await asyncio.to_thread(
                create_remote_hwpx_document,
                filename,
                title,
                document_content,
                sections,
                tables,
                formatting,
            )
        except Exception as exc:
            logger.exception("Remote HWPX creation failed")
            return {
                "success": False,
                "filename": filename,
                "mime_type": HWPX_MIME_TYPE,
                "download_url": None,
                "error": str(exc),
            }

    file_tool_meta = {"openai/fileParams": ["template_file"]}
    fill_tool_meta = {
        **file_tool_meta,
        "ui": {"resourceUri": WIDGET_URI, "visibility": ["model", "app"]},
        "openai/outputTemplate": WIDGET_URI,
        "openai/widgetAccessible": True,
    }

    @mcp.tool(
        title="Inspect HWPX template",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        meta=file_tool_meta,
    )
    async def inspect_hwpx_template(template_file: OpenAIFile) -> dict[str, Any]:
        """Inspect an uploaded HWPX template without modifying it.

        ``template_file`` accepts the standard ChatGPT file parameter object
        with ``download_url``, ``file_id``, optional ``mime_type``, and optional
        ``file_name``. Other MCP clients can provide the same object shape.
        """
        try:
            return await asyncio.to_thread(
                inspect_remote_hwpx_template, template_file
            )
        except Exception as exc:
            logger.exception("HWPX template inspection failed")
            return {"success": False, "error": str(exc)}

    @mcp.tool(
        title="Fill HWPX template",
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
        meta=fill_tool_meta,
    )
    async def fill_hwpx_template(
        template_file: OpenAIFile,
        filename: str,
        field_values: dict[str, str] | None = None,
        replacements: dict[str, str] | None = None,
        table_cell_values: list[dict[str, Any]] | None = None,
        paragraphs: list[dict[str, Any]] | None = None,
        instructions: str = "",
    ) -> dict[str, Any]:
        """Fill a supplied HWPX while preserving its package and styles.

        Field values target native HWPX fields. Replacements target literal
        text or placeholders. Table cells may be addressed by zero-based
        ``table_index``, ``row``, ``column`` or by ``label`` (the next cell is
        filled). Paragraph items accept ``content`` and optional ``after_text``.
        The original upload is never modified.
        """
        try:
            return await asyncio.to_thread(
                fill_remote_hwpx_template,
                template_file,
                filename,
                field_values,
                replacements,
                table_cell_values,
                paragraphs,
                instructions,
            )
        except Exception as exc:
            logger.exception("HWPX template fill failed")
            return {
                "success": False,
                "filename": filename,
                "mime_type": HWPX_MIME_TYPE,
                "download_url": None,
                "error": str(exc),
            }

    register_hwpx_widget(mcp)


def build_download_router() -> APIRouter:
    """Build health, browser upload, and public file-download routes."""
    router = APIRouter()

    @router.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": "hwpx-remote-mcp",
            "template_upload_url": f"{_public_base_url()}/upload",
        }

    @router.get("/upload", include_in_schema=False, response_class=HTMLResponse)
    async def upload_page() -> HTMLResponse:
        return HTMLResponse(_UPLOAD_PAGE)

    @router.post("/uploads", include_in_schema=False)
    async def upload_template(request: Request) -> dict[str, Any]:
        raw_filename = request.headers.get("x-file-name", "")
        decoded_filename = unquote(raw_filename)
        if not raw_filename or not decoded_filename.lower().endswith(".hwpx"):
            raise HTTPException(
                status_code=400, detail="HWPX 파일만 업로드할 수 있습니다"
            )
        try:
            filename = normalize_filename(decoded_filename)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        content_type = (
            request.headers.get("content-type", "")
            .split(";", 1)[0]
            .strip()
            .lower()
        )
        if content_type not in _UPLOAD_MIME_TYPES:
            raise HTTPException(
                status_code=415, detail="지원하지 않는 MIME 형식입니다"
            )

        limit = _max_upload_bytes()
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > limit:
                    raise HTTPException(
                        status_code=413, detail="파일 크기 제한을 초과했습니다"
                    )
            except ValueError as exc:
                raise HTTPException(
                    status_code=400, detail="잘못된 Content-Length입니다"
                ) from exc

        with tempfile.TemporaryDirectory(prefix="hwpx-upload-") as temp_dir:
            temp_path = Path(temp_dir) / filename
            total = 0
            with temp_path.open("wb") as output:
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > limit:
                        raise HTTPException(
                            status_code=413, detail="파일 크기 제한을 초과했습니다"
                        )
                    output.write(chunk)
            if total == 0:
                raise HTTPException(
                    status_code=400, detail="빈 파일은 업로드할 수 없습니다"
                )

            validation = validate_hwpx_archive(temp_path)
            if not validation.get("valid"):
                raise HTTPException(
                    status_code=400,
                    detail="올바른 HWPX가 아닙니다: "
                    + "; ".join(validation.get("errors", [])),
                )
            result = publish_hwpx_file(temp_path, filename)

        document_id = (
            result["download_url"]
            .split("/downloads/", 1)[1]
            .split("/", 1)[0]
        )
        file_object = {
            "download_url": result["download_url"],
            "file_id": f"upload_{document_id}",
            "mime_type": HWPX_MIME_TYPE,
            "file_name": filename,
        }
        result["template_file"] = file_object
        result["claude_prompt"] = (
            "이 HWPX 서식을 inspect_hwpx_template로 분석한 뒤 기존 표, 글꼴, 문단, "
            "이미지, 머리말/꼬리말을 최대한 유지하여 fill_hwpx_template로 작성해줘. "
            "template_file 인수에는 다음 객체를 그대로 사용해: "
            + json.dumps(file_object, ensure_ascii=False)
        )
        return result

    @router.get("/downloads/{document_id}/{filename}", include_in_schema=False)
    async def download_document(
        document_id: str,
        filename: str,
        expires: int,
        signature: str,
    ) -> FileResponse:
        if not re.fullmatch(r"[0-9a-f]{32}", document_id):
            raise HTTPException(status_code=404, detail="Document not found")
        try:
            safe_filename = normalize_filename(filename)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Document not found") from exc

        if expires < int(time.time()):
            expired_dir = (get_output_dir() / document_id).resolve()
            if expired_dir.parent == get_output_dir() and expired_dir.is_dir():
                shutil.rmtree(expired_dir, ignore_errors=True)
            raise HTTPException(status_code=410, detail="Download link expired")
        expected_signature = _signature(document_id, safe_filename, expires)
        if not hmac.compare_digest(signature, expected_signature):
            raise HTTPException(status_code=403, detail="Invalid download signature")

        output_dir = get_output_dir()
        file_path = (output_dir / document_id / safe_filename).resolve()
        expected_parent = (output_dir / document_id).resolve()
        if file_path.parent != expected_parent or not file_path.is_file():
            raise HTTPException(status_code=404, detail="Document not found")

        return FileResponse(
            path=file_path,
            media_type=HWPX_MIME_TYPE,
            filename=safe_filename,
        )

    return router
