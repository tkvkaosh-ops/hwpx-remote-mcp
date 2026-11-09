"""Exercise a standard Remote MCP server without calling any model API."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import zipfile
from pathlib import Path

import httpx
import jsonschema
from defusedxml import ElementTree
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def validate_document(data: bytes) -> None:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.testzip() is None
        assert archive.namelist()[0] == "mimetype"
        assert archive.read("mimetype") == b"application/hwp+zip"
        assert archive.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
        for info in archive.infolist():
            if info.filename.lower().endswith((".xml", ".hpf")):
                assert info.file_size <= 32 * 1024 * 1024
                ElementTree.fromstring(archive.read(info.filename))


async def verify(url: str, output: Path) -> None:
    base = url.rstrip("/").removesuffix("/mcp")
    endpoint = base + "/mcp"
    output.mkdir(parents=True, exist_ok=True)
    report = {"endpoint": endpoint, "uses_model_api": False, "checks": []}

    async with httpx.AsyncClient(timeout=90) as http:
        async with streamable_http_client(endpoint) as (reader, writer, _):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                listing = await session.list_tools()
                names = sorted(tool.name for tool in listing.tools)
                assert names == ["create_hwpx_document", "fill_hwpx_template", "inspect_hwpx_template"]
                for tool in listing.tools:
                    jsonschema.Draft202012Validator.check_schema(tool.inputSchema)
                    if tool.name != "create_hwpx_document":
                        descriptor = tool.model_dump(by_alias=True)
                        assert descriptor["_meta"]["openai/fileParams"] == ["template_file"]
                report["checks"].extend(["initialize", "tools/list", "tool schemas", "OpenAI fileParams"])

                async def call(name, arguments):
                    result = await session.call_tool(name, arguments)
                    assert not result.isError
                    data = result.structuredContent
                    if data is None:
                        data = json.loads(next(item.text for item in result.content if item.type == "text"))
                    assert data["success"], data
                    report["checks"].append(name)
                    return data

                async def download(result, filename):
                    response = await http.get(result["download_url"])
                    response.raise_for_status()
                    assert len(response.content) == result["file_size"]
                    validate_document(response.content)
                    (output / filename).write_bytes(response.content)
                    return response.content

                created = await call("create_hwpx_document", {
                    "filename": "교육결과보고서_빈서식.hwpx",
                    "title": "{{교육명}} 결과보고서",
                    "paragraphs": ["담당자: {{담당자}}"],
                    "sections": [{"heading": "교육개요", "content": "교육 결과를 정리해요."}],
                    "tables": [{"headers": ["항목", "내용"], "rows": [["교육대상", ""], ["교육일시", ""]]}],
                })
                original = await download(created, "01_빈서식.hwpx")
                uploaded = await http.post(base + "/uploads", content=original, headers={
                    "Content-Type": "application/vnd.hancom.hwpx", "X-File-Name": "template.hwpx",
                })
                uploaded.raise_for_status()
                file_info = uploaded.json()["template_file"]
                inspection = await call("inspect_hwpx_template", {"template_file": file_info})
                assert set(inspection["placeholders"]) == {"교육명", "담당자"}

                result = await call("fill_hwpx_template", {
                    "template_file": file_info, "filename": "교육결과보고서_완성본.hwpx",
                    "field_values": {"교육명": "AI 활용 교육", "담당자": "매실한입"},
                    "table_cell_values": [{"label": "교육대상", "value": "임직원 12명"}],
                    "paragraphs": [{"after_text": "교육개요", "content": "문서 작성과 주문 정리를 실습했어요."}],
                })
                completed = await download(result, "02_완성본.hwpx")
                original_again = await http.get(file_info["download_url"])
                original_again.raise_for_status()
                assert hashlib.sha256(original).digest() == hashlib.sha256(original_again.content).digest()
                with zipfile.ZipFile(io.BytesIO(original)) as before, zipfile.ZipFile(io.BytesIO(completed)) as after:
                    for name in before.namelist():
                        if name not in result["modified_package_parts"]:
                            assert before.read(name) == after.read(name), name
                    assert "임직원 12명" in after.read("Contents/section0.xml").decode("utf-8")
                report["checks"].extend(["upload", "download", "ZIP/XML", "source unchanged", "unmodified package parts preserved"])
                report["success"] = True
                report["result"] = result

    (output / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("PASS: standard Streamable HTTP MCP; create → upload → inspect → fill → download")
    print("No OpenAI/Anthropic model API call or model API key was used.")
    print("Results:", output.resolve())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="https://your-host/mcp")
    parser.add_argument("--output", type=Path, default=Path("artifacts/remote-verification"))
    arguments = parser.parse_args()
    asyncio.run(verify(arguments.url, arguments.output))
