/* // 启动序列（最后加载）：顶层初始化调用与事件流连接 */

initLayoutPrefs();
initSplitters();
renderModelSelector();
fetchModels();
loadSkills();
setupConnectionEventListeners();
setupCameraEventListeners();
initSystemSettingsDrag();

// 异步加载后端连接设置（后端已在启动时根据 auto_connect 自动尝试连接）。
loadConnectionSettings();
loadCameraSettings();
loadApplicationSettings();


connectEventStream();
initAirSimTemplatesEvents();

// Initial render with defaults so the page is never blank
function renderInitialDefaults() {
  renderChat([], null, {});
  if (latestState) updateMapView(latestState);
}
renderInitialDefaults();

// Refresh state, but always render something even on failure
async function initialRefresh() {
  try {
    latestState = applyCachedSessionHistory(await api("/api/state"));
    render(latestState);
    await loadCurrentSessionHistory();
  } catch (_) {
    // Keep default render from above
  }
}
initialRefresh();

restartMainTelemetryRefresh();
setInterval(() => refresh().catch(() => {}), 6000);
window.addEventListener("resize", () => {
  if (maplibreMap) maplibreMap.resize();
});
