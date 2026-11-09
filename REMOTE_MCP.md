# HWPX Remote MCP

이 Remote MCP 프로필은 OpenAI·Anthropic 모델 API를 호출하지 않으며 모델 API 키가 필요하지 않다. 자연어 해석은 연결한 ChatGPT·Claude가 수행하고, 서버는 구조화된 작업 인수를 처리한다. `DOWNLOAD_SIGNING_KEY`(기존 `HWPX_SIGNING_KEY`도 지원)는 다운로드 링크 보호용 서버 내부 비밀값이며 모델 API 키가 아니다.

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

MCP endpoint는 `http://localhost:8000/mcp`, health endpoint는 `http://localhost:8000/health`, ChatGPT·Claude 공통 업로드 페이지는 `http://localhost:8000/upload`다.

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

새 문서는 `create_hwpx_document`로 바로 만든다. 사용 중인 Claude 환경에서 HWPX 첨부 파일을 MCP에 전달할 수 없으면 공통 업로드 페이지를 사용한다.

1. `https://<domain>/upload`에서 HWPX 원본을 선택한다.
2. 서버가 ZIP/XML 구조를 검사하고 만료되는 서명 URL과 `template_file` 객체를 만든다.
3. 페이지의 **요청문 복사**를 누르고 Claude 채팅에 붙여넣는다.
4. Claude가 `inspect_hwpx_template`와 `fill_hwpx_template`를 호출하면 완성본 다운로드 URL을 돌려준다.

업로드된 원본도 UUID로 격리되고 결과 파일과 동일한 만료 정책을 적용받는다. 서버 내부 경로는 Claude에 노출하지 않는다.

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
- 만료 artifact는 저장된 만료 정보에 따라 약 60초 간격으로 정리
- DNS 검사 후 검증한 IP에 직접 연결하며 원래 호스트의 TLS 인증서와 SNI를 유지
- 이 서버가 발급한 서명 URL은 검증 후 해당 파일만 직접 복사하며 내부망 다운로드 예외가 필요 없음
- 만료 값을 변조한 요청은 아직 유효한 파일을 삭제하지 않음
- 서식 채우기는 부분 글자 모양을 유지하며 수정된 문단의 줄 배치 캐시만 재계산하도록 제거
- 모델 응답과 HTTP access log에 임시 서버 경로나 서명 토큰을 노출하지 않음

## API 키 없는 검증

`pytest hwpx_mcp/tests/test_remote_server.py hwpx_mcp/tests/test_remote_documents.py hwpx_mcp/tests/test_hwpx_templates.py hwpx_mcp/tests/test_remote_hardening.py -q`로 Remote MCP 범위의 회귀 테스트를 실행한다. 공식 MCP SDK 클라이언트나 MCP Inspector는 모델 API 키 없이 initialize, tools/list, tools/call, 다운로드를 검증할 수 있다. 위 OpenAI Responses API 예제의 실호출은 별도 API 인증이 있는 경우에만 수행한다.

Linux remote 서버는 native binary `.hwp` 편집을 제공하지 않는다. 서식 보존 편집 입력은 `.hwpx`를 사용한다.
