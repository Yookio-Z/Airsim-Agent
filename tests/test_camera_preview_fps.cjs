const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

function cameraHarness() {
  let now = 10000;
  let streams = 1;
  const delays = [];
  const context = vm.createContext({
    Date: { now: () => now },
    setTimeout: (_, delay) => { delays.push(delay); return 1; },
    clearTimeout: () => {},
    CAMERA_STREAM_ERROR_INTERVAL_MS: 1400,
    // renderCameraPerf 现在要读遥测里的检测状态（与检测徽标同源）
    latestState: { tool_runtime: { perception: {} } },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/ui/static/ui-camera.js'), 'utf8'), context);
  context.activeCameraStreams = () => Array(streams).fill({});
  context.cameraViewerIsVisible = () => true;
  return { context, delays, tick: (ms) => { now += ms; }, streams: (count) => { streams = count; } };
}

test('repeated cache frames are not counted and stale samples expire', () => {
  const h = cameraHarness();
  const win = { frameTimes: [], lastFrameTimestamp: null };
  assert.equal(h.context.noteCameraFrameArrival(win, { frame_timestamp: 1, preview_cached: true }), true);
  h.tick(100);
  assert.equal(h.context.noteCameraFrameArrival(win, { frame_timestamp: 1, preview_cached: true }), false);
  assert.equal(win.frameTimes.length, 1);
  h.tick(400);
  h.context.noteCameraFrameArrival(win, { frame_timestamp: 2, preview_cached: true });
  assert.equal(h.context.cameraDeliveryFps(win), 2);
  h.tick(500);
  h.context.noteCameraFrameArrival(win, { frame_timestamp: 3, preview_cached: true });
  assert.equal(h.context.cameraDeliveryFps(win), 2);
  h.tick(3100);
  assert.equal(h.context.cameraDeliveryFps(win), 0);
});

test('only cached previews use fast bounded polling; errors retain backoff', () => {
  const h = cameraHarness();
  const win = { streamActive: true, settings: { source: 'airsim' }, el: { dataset: { state: 'ready' } } };
  h.context.scheduleCameraFrame(win);
  assert.equal(h.delays.pop(), 700);
  win.previewCached = true;
  h.context.scheduleCameraFrame(win);
  assert.equal(h.delays.pop(), 100);
  h.streams(4);
  h.context.scheduleCameraFrame(win);
  assert.equal(h.delays.pop(), 400);
  win.el.dataset.state = 'error';
  h.context.scheduleCameraFrame(win);
  assert.equal(h.delays.pop(), 1400);
  win.streamActive = false;
  h.context.scheduleCameraFrame(win);
  assert.equal(h.delays.length, 0);
});

test('perf badge shows delivered frames only (检测已移除，徽标不再报识别速率)', () => {
  const h = cameraHarness();
  const win = { streamActive: true, perfEl: {}, frameTimes: [9000, 9500, 10000] };
  h.context.renderCameraPerf(win, { fps: 10, detect_fps: 3 });
  assert.match(win.perfEl.innerHTML, /FPS 2\.0/);
  // 检测已从产品移除：无论预览 meta 还是遥测，都不再出现"识别/检测"徽标
  assert.doesNotMatch(win.perfEl.innerHTML, /识别|检测/);
  assert.match(win.perfEl.title, /后台采集 10\.0/);
  h.context.latestState = { tool_runtime: { perception: { detect_fps: 3.1 } } };
  h.context.renderCameraPerf(win, { fps: 10, detect_fps: 9.9 });
  assert.doesNotMatch(win.perfEl.innerHTML, /识别|检测|3\.1|9\.9/);
  win.streamActive = false;
  h.context.renderCameraPerf(win);
  assert.equal(win.perfEl.hidden, true);
});
