"""Smoke-test the public HWPX Remote MCP with the OpenAI Responses API.

Required environment variables:
    OPENAI_API_KEY
    MCP_SERVER_URL          e.g. https://example.up.railway.app/mcp

Optional template scenario variables:
    TEMPLATE_DOWNLOAD_URL   Public HTTPS URL for a source .hwpx
    TEMPLATE_FILE_ID        Any stable source identifier
    TEMPLATE_FILE_NAME      Defaults to template.hwpx
    OPENAI_MODEL            Defaults to gpt-6-astra
"""

from __future__ import annotations

import json
import os
import sys

from openai import OpenAI


def remote_mcp_tool(server_url: str, allowed_tools: list[str]) -> dict:
    return {
        "type": "mcp",
        "server_label": "hwpx_documents",
        "server_description": (
            "Creates HWPX files and inspects or fills uploaded HWPX templates "
            "while preserving the original package and formatting."
        ),
        "server_url": server_url,
        "require_approval": "never",
        "allowed_tools": allowed_tools,
    }


def summarize_response(label: str, response) -> None:
    print(f"\n[{label}]")
    print(response.output_text)
    for item in response.output:
        item_type = getattr(item, "type", "")
        if item_type in {"mcp_list_tools", "mcp_call"}:
            print(item.model_dump_json(indent=2))


def main() -> int:
    server_url = os.environ.get("MCP_SERVER_URL", "").strip()
    if not server_url:
        print("MCP_SERVER_URL is required", file=sys.stderr)
        return 2

    model = os.environ.get("OPENAI_MODEL", "gpt-6-astra")
    client = OpenAI()

    create_response = client.responses.create(
        model=model,
        tools=[remote_mcp_tool(server_url, ["create_hwpx_document"])],
        input=(
            "AI 활용 교육 결과보고서를 HWPX로 만들어줘. 제목은 'AI 활용 교육 "
            "결과보고서'이고 교육개요, 교육내용, 교육성과, 향후계획 섹션과 "
            "간단한 표 1개를 포함해. 완료되면 다운로드 URL을 알려줘."
        ),
    )
    summarize_response("create", create_response)

    template_url = os.environ.get("TEMPLATE_DOWNLOAD_URL", "").strip()
    if not template_url:
        print(
            "\n[template] skipped: TEMPLATE_DOWNLOAD_URL is not set",
            file=sys.stderr,
        )
        return 0

    file_object = {
        "download_url": template_url,
        "file_id": os.environ.get("TEMPLATE_FILE_ID", "file_api_template"),
        "mime_type": "application/vnd.hancom.hwpx",
        "file_name": os.environ.get("TEMPLATE_FILE_NAME", "template.hwpx"),
    }
    template_response = client.responses.create(
        model=model,
        tools=[
            remote_mcp_tool(
                server_url,
                ["inspect_hwpx_template", "fill_hwpx_template"],
            )
        ],
        input=(
            "아래 HWPX 서식을 먼저 inspect_hwpx_template로 분석한 다음, 기존 서식을 "
            "유지하면서 교육 결과를 채워 새 HWPX를 만들어줘. template_file 인수에는 "
            f"다음 객체를 그대로 사용해: {json.dumps(file_object, ensure_ascii=False)}"
        ),
    )
    summarize_response("inspect-and-fill", template_response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
