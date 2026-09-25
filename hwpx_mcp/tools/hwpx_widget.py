"""Optional MCP Apps / ChatGPT widget for HWPX template workflows."""

from __future__ import annotations

from typing import Any

WIDGET_URI = "ui://hwpx/template-filler.html"

WIDGET_HTML = r"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
    :root { color-scheme: light dark; font-family: system-ui, sans-serif; }
    body { margin: 0; padding: 18px; }
    main { display: grid; gap: 12px; }
    h2 { margin: 0; font-size: 18px; }
    .row { display: flex; gap: 8px; flex-wrap: wrap; }
    button, input, textarea { font: inherit; }
    button { border: 0; border-radius: 10px; padding: 10px 14px; cursor: pointer; }
    button.primary { background: #2563eb; color: white; }
    button:disabled { opacity: .5; cursor: not-allowed; }
    input, textarea { box-sizing: border-box; width: 100%; border: 1px solid #9ca3af; border-radius: 8px; padding: 9px; background: transparent; }
    textarea { min-height: 110px; resize: vertical; }
    small, #status { opacity: .78; }
  </style>
</head>
<body>
<main>
  <h2>HWPX 서식 채우기</h2>
  <div class="row">
    <button id="select" class="primary">HWPX 서식 선택</button>
    <input id="fallback" type="file" accept=".hwpx,application/vnd.hancom.hwpx" hidden>
  </div>
  <small id="selected">선택된 파일 없음</small>
  <input id="filename" value="완성본.hwpx" aria-label="완성본 파일명">
  <textarea id="replacements" aria-label="치환 값">{
  "{{항목}}": "입력할 내용"
}</textarea>
  <div class="row">
    <button id="create" class="primary" disabled>완성본 생성</button>
    <button id="download" disabled>완성본 다운로드</button>
  </div>
  <div id="status">ChatGPT 파일 기능을 확인하는 중입니다.</div>
</main>
<script>
(() => {
  const host = window.openai;
  const selectButton = document.querySelector('#select');
  const fallback = document.querySelector('#fallback');
  const createButton = document.querySelector('#create');
  const downloadButton = document.querySelector('#download');
  const selected = document.querySelector('#selected');
  const status = document.querySelector('#status');
  let templateFile = null;
  let resultUrl = null;

  const unpack = (result) => {
    if (result?.structuredContent) return result.structuredContent;
    const text = result?.content?.find?.((item) => item.type === 'text')?.text;
    if (text) try { return JSON.parse(text); } catch (_) {}
    return result || {};
  };

  async function authorizeFile(file) {
    const fileId = file.fileId || file.file_id;
    if (!host?.getFileDownloadUrl) throw new Error('파일 다운로드 URL 기능을 사용할 수 없습니다.');
    const resolved = await host.getFileDownloadUrl({ fileId });
    return {
      download_url: resolved.downloadUrl || resolved.download_url,
      file_id: fileId,
      mime_type: file.mimeType || file.mime_type || 'application/vnd.hancom.hwpx',
      file_name: file.fileName || file.file_name || 'template.hwpx'
    };
  }

  selectButton.addEventListener('click', async () => {
    try {
      status.textContent = '서식을 선택하는 중…';
      if (host?.selectFiles) {
        const files = await host.selectFiles();
        if (!files?.length) throw new Error('파일이 선택되지 않았습니다.');
        templateFile = await authorizeFile(files[0]);
      } else {
        fallback.click();
        return;
      }
      selected.textContent = templateFile.file_name;
      createButton.disabled = false;
      status.textContent = '서식이 준비되었습니다.';
    } catch (error) { status.textContent = error.message; }
  });

  fallback.addEventListener('change', async () => {
    try {
      const file = fallback.files?.[0];
      if (!file || !host?.uploadFile) throw new Error('파일 업로드 기능을 사용할 수 없습니다.');
      const uploaded = await host.uploadFile(file, { library: true });
      templateFile = await authorizeFile({ ...uploaded, fileName: file.name, mimeType: file.type });
      selected.textContent = templateFile.file_name;
      createButton.disabled = false;
      status.textContent = '서식이 준비되었습니다.';
    } catch (error) { status.textContent = error.message; }
  });

  createButton.addEventListener('click', async () => {
    try {
      if (!host?.callTool) throw new Error('도구 호출 기능을 사용할 수 없습니다.');
      status.textContent = '완성본을 생성하는 중…';
      const replacements = JSON.parse(document.querySelector('#replacements').value || '{}');
      const result = unpack(await host.callTool('fill_hwpx_template', {
        template_file: templateFile,
        filename: document.querySelector('#filename').value || '완성본.hwpx',
        replacements
      }));
      if (!result.success) throw new Error(result.error || '생성에 실패했습니다.');
      resultUrl = result.download_url;
      downloadButton.disabled = false;
      status.textContent = `완성: ${result.filename}`;
    } catch (error) { status.textContent = error.message; }
  });

  downloadButton.addEventListener('click', async () => {
    if (!resultUrl) return;
    if (host?.openExternal) await host.openExternal({ href: resultUrl, redirectUrl: false });
    else window.open(resultUrl, '_blank', 'noopener,noreferrer');
  });

  status.textContent = host ? 'HWPX 서식을 선택해 주세요.' : '이 UI는 MCP Apps/ChatGPT 호스트에서 동작합니다.';
})();
</script>
</body>
</html>"""


def register_hwpx_widget(mcp: Any) -> None:
    """Register the optional standard MCP Apps HTML resource."""

    @mcp.resource(
        WIDGET_URI,
        name="HWPX template filler",
        title="HWPX template filler",
        description="Select, fill, and download an HWPX template.",
        mime_type="text/html;profile=mcp-app",
        meta={
            "ui": {
                "prefersBorder": True,
                "csp": {"connectDomains": [], "resourceDomains": []},
            },
            "openai/widgetDescription": "HWPX 서식을 선택하고 완성본을 다운로드하는 화면",
            "openai/widgetPrefersBorder": True,
        },
    )
    def hwpx_template_filler_widget() -> str:
        return WIDGET_HTML
