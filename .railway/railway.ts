import { defineRailway, github, preserve, project, service, volume } from "railway/iac";

export default defineRailway(() => {
  const hwpxRemoteMcpVolume = volume("hwpx-remote-mcp-volume", { alerts: { usage: { "100": {}, "80": {}, "95": {} } }, allowOnlineResize: true, region: "sfo", sizeMB: 500 });
  const hwpxRemoteMcp = service("hwpx-remote-mcp", {
    source: github("tkvkaosh-ops/hwpx-remote-mcp"),
    healthcheck: "/health",
    healthcheckTimeout: 120,
    replicas: { "sfo": 1 },
    volumeMounts: { "/app/output": hwpxRemoteMcpVolume },
    env: { HWPX_DOWNLOAD_TTL_SECONDS: preserve(), HWPX_MAX_UPLOAD_BYTES: preserve(), HWPX_OUTPUT_DIR: preserve(), HWPX_SIGNING_KEY: preserve(), PUBLIC_BASE_URL: preserve(), RAILWAY_RUN_UID: preserve() },
  });

  return project("hwpx-remote-mcp", {
    resources: [hwpxRemoteMcp, hwpxRemoteMcpVolume],
  });
});
