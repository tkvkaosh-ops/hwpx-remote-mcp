# HWPX Remote MCP

이 서버는 Claude, ChatGPT, OpenAI Responses API가 같은 `/mcp` endpoint로 사용할 수 있는 표준 Streamable HTTP MCP 서버다. 원격 프로필은 아래 세 도구만 공개한다.

- `create_hwpx_document`: 새 HWPX 생성
- `inspect_hwpx_template`: 첨부 HWPX의 필드, placeholder, 문단, 표, 보존 자산 분석
- `fill_hwpx_template`: 원본 ZIP package를 복제하고 필요한 section XML만 수정해 새 HWPX 생성

## Local run

```powershell
$env:MCP_TRANSPORT = "streamable-http"
$env:MCP_HOST = "0.0.0.0"
$env:MCP_PORT = "8000"
$env:MCP_PATH = "/mcp"
$env:MCP_STATELESS = "true"
$env:MCP_JSON_RESPONSE = "true"
$env:PUBLIC_BASE_URL = "http://localhost:8000"
$env:HWPX_OUTPUT_DIR = "$PWD/output"
$env:DOWNLOAD_SIGNING_KEY = "replace-with-a-long-random-secret"
python -m hwpx_mcp.remote_server
```

MCP endpoint는 `http://localhost:8000/mcp`, health endpoint는 `http://localhost:8000/health`다.

## Docker

```bash
docker build -t hwpx-remote-mcp .
docker run --rm -p 8000:8000 \
  -e PUBLIC_BASE_URL=http://localhost:8000 \
  -e DOWNLOAD_SIGNING_KEY=replace-with-a-long-random-secret \
  -v hwpx-output:/app/output \
  hwpx-remote-mcp
```

## File input

`inspect_hwpx_template`와 `fill_hwpx_template`의 `template_file`은 다음 공통 객체를 받는다.

```json
{
  "download_url": "https://files.example.com/template.hwpx",
  "file_id": "file_123",
  "mime_type": "application/vnd.hancom.hwpx",
  "file_name": "template.hwpx"
}
```

두 도구의 descriptor에는 ChatGPT가 첨부 파일을 이 객체로 전달할 수 있도록 `_meta["openai/fileParams"] = ["template_file"]`가 포함된다. 다른 MCP client도 같은 JSON shape를 사용할 수 있다.

## Template fill controls

- `field_values`: native HWPX field name → value
- `replacements`: 기존 문자열 또는 `{{placeholder}}` → 새 문자열
- `table_cell_values`: `{table_index,row,column,value}` 또는 `{label,value}`
- `paragraphs`: `{after_text,content}`
- `instructions`: 모델이 구조화 인수로 변환한 원래 사용자 지시의 참고 문자열

원본 업로드는 임시 디렉터리에서 읽고 작업 종료 시 삭제한다. 완성본은 새 UUID 디렉터리에 저장되며, 응답에는 서버 절대경로 대신 HMAC 서명과 만료시각이 포함된 다운로드 URL만 반환된다.

## Railway

`railway.toml`과 `Dockerfile`이 포함되어 있다. 배포 환경에는 최소한 다음 값을 설정한다.

```text
PUBLIC_BASE_URL=https://<service-domain>
DOWNLOAD_SIGNING_KEY=<long-random-secret>
HWPX_OUTPUT_DIR=/app/output
```

배포 재시작 뒤에도 유효기간 내 결과 파일을 유지하려면 Railway volume을 `/app/output`에 mount한다.

## Claude

Claude Custom Connector에 공개 HTTPS MCP URL(`https://<domain>/mcp`)을 등록한다. 인증 없는 개발용 endpoint이므로 연결 후 세 도구가 표시되는지 확인한다.

## ChatGPT

ChatGPT의 developer mode에서 같은 HTTPS `/mcp` URL로 앱을 만든다. `template_file`은 ChatGPT file parameter로 선언되어 있으며, 선택 UI는 지원되는 경우 `window.openai.selectFiles`, `uploadFile`, `getFileDownloadUrl`을 feature detection으로 사용한다.

## OpenAI Responses API

`examples/openai_responses_mcp.py`를 사용한다.

```bash
pip install openai
export OPENAI_API_KEY=...
export MCP_SERVER_URL=https://<domain>/mcp
python examples/openai_responses_mcp.py
```

서식 채우기 시나리오는 `TEMPLATE_DOWNLOAD_URL`, `TEMPLATE_FILE_ID`, `TEMPLATE_FILE_NAME`도 설정한다.

## Security defaults

- HTTPS source URL only; private, loopback, link-local, reserved IP 차단
- `.hwpx` extension과 allowlisted MIME 검사
- upload 기본 20 MiB, 압축 해제 합계 기본 100 MiB 제한
- ZIP entry path traversal 차단
- ZIP/XML 구조와 HWPX mimetype 검사
- 임시 작업공간 자동 삭제
- 결과 UUID 분리, HMAC 서명 다운로드, 기본 24시간 만료
- 만료 artifact는 다음 생성 요청 또는 만료 URL 접근 시 삭제

Linux remote 서버는 native binary `.hwp` 편집을 제공하지 않는다. 서식 보존 편집 입력은 `.hwpx`를 사용한다.
