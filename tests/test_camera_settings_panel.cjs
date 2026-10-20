// 摄像头设置面板（2026-10-20 精简）+ 相机清单来源。
//
// 两条契约：
//   1) 设置面板只保留 RTSP 地址（相机/机型/图像类型/超时由摄像头面板维护）；
//   2) 相机下拉只列 AirSim settings.json 里真实声明的相机，不再写死 0..4。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const STATIC = path.join(__dirname, '../src/ui/static');
const INDEX_HTML = fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8');

class FakeOption {
  constructor(text, value) {
    this.textContent = String(text);
    this.value = String(value);
  }
}

function cameraHarness() {
  const context = vm.createContext({
    document: {
      getElementById: () => null,
      addEventListener() {},
      querySelector: () => null,
      querySelectorAll: () => [],
      body: { classList: { add() {}, remove() {}, toggle() {}, contains: () => false } },
      documentElement: { style: {} },
    },
    localStorage: {
      store: {},
      getItem(key) { return Object.prototype.hasOwnProperty.call(this.store, key) ? this.store[key] : null; },
      setItem(key, value) { this.store[key] = String(value); },
      removeItem(key) { delete this.store[key]; },
    },
    window: {
      addEventListener() {},
      matchMedia: () => ({ matches: false, addEventListener() {} }),
      setInterval: () => 1,
      clearInterval() {},
      setTimeout: () => 1,
      clearTimeout() {},
      requestAnimationFrame: () => 1,
    },
    Option: FakeOption,
    console,
    setTimeout,
    clearTimeout,
    JSON,
    Math,
    Date,
    Number,
    String,
    Boolean,
    Object,
    Array,
    Map,
    Set,
  });
  vm.runInContext(fs.readFileSync(path.join(STATIC, 'ui-core.js'), 'utf8'), context);
  vm.runInContext(fs.readFileSync(path.join(STATIC, 'ui-camera.js'), 'utf8'), context);
  return context;
}

function run(context, source) {
  vm.runInContext(source, context);
  return vm.runInContext('globalThis.__out', context);
}

test('设置面板只剩 RTSP 地址一个输入', () => {
  for (const gone of ['cameraSource', 'cameraRtspTransport', 'cameraName', 'cameraVehicle', 'cameraImageType', 'cameraTimeout']) {
    assert.equal(INDEX_HTML.includes(`id="${gone}"`), false, `${gone} 不应再出现在设置面板里`);
  }
  assert.match(INDEX_HTML, /id="cameraRtspUrl"/);
  assert.match(INDEX_HTML, /id="cameraCurrentSource"/);
});

test('设置面板不再有「查看画面」按钮', () => {
  assert.equal(INDEX_HTML.includes('cameraCaptureFromSettingsBtn'), false);
  assert.equal(INDEX_HTML.includes('查看画面'), false);
});

test('摄像头窗口的层级始终低于设置面板', () => {
  const outcome = run(cameraHarness(), `
    cameraWindows.clear();
    const first = { el: { hidden: false, style: {} } };
    const second = { el: { hidden: false, style: {} } };
    cameraWindows.set("camera_1", first);
    cameraWindows.set("camera_2", second);
    focusCameraWindow(second);
    const afterSecond = [Number(first.el.style.zIndex), Number(second.el.style.zIndex)];
    focusCameraWindow(first);
    const afterFirst = [Number(first.el.style.zIndex), Number(second.el.style.zIndex)];
    globalThis.__out = { afterSecond, afterFirst };
  `);

  const [firstZ, secondZ] = Array.from(outcome.afterSecond);
  assert.ok(secondZ > firstZ, `被点窗口应在最前: ${firstZ} < ${secondZ}`);
  const [firstZ2, secondZ2] = Array.from(outcome.afterFirst);
  assert.ok(firstZ2 > secondZ2, `换点另一个窗口后顺序应翻转: ${secondZ2} < ${firstZ2}`);
  // .settings-backdrop 是 29、.modal-overlay 是 40：摄像头窗口必须低于 29
  for (const z of [firstZ, secondZ, firstZ2, secondZ2]) {
    assert.ok(z < 29, `摄像头窗口 z-index=${z} 不得盖过设置面板`);
    assert.ok(z > 8, `摄像头窗口 z-index=${z} 应仍在底图之上`);
  }
});

test('相机下拉不再写死 0..4', () => {
  for (const label of ['0 · 前视', '1 · 前右', '2 · 前左', '3 · 下视', '4 · 后视']) {
    assert.equal(INDEX_HTML.includes(`>${label}<`), false, `${label} 不应再写死在 HTML 里`);
  }
});

test('相机选项只来自 AirSim settings.json 的清单', () => {
  const outcome = run(cameraHarness(), `
    airsimCameraCatalog = { by_vehicle: { PX4: ["CameraImage"] }, default: ["CameraImage"] };
    function fakeSelect() {
      const options = [];
      return {
        options,
        value: "",
        set textContent(value) { if (value === "") options.length = 0; },
        add(option) { options.push(option); },
      };
    }
    const select = fakeSelect();
    const win = {
      cameraSelect: select,
      vehicleSelect: { value: "PX4", options: [] },
      settings: { camera_name: "CameraImage" },
      viewJoinedAxis: true,
    };
    const name = syncCameraCameraOptions(win, "CameraImage");
    globalThis.__out = {
      name,
      values: select.options.map((option) => option.value),
      labels: select.options.map((option) => option.textContent),
    };
  `);

  assert.equal(outcome.name, 'CameraImage');
  assert.deepEqual(Array.from(outcome.values), ['CameraImage']);
  assert.deepEqual(Array.from(outcome.labels), ['CameraImage']);
});

test('默认相机清单仍是 0..4（settings.json 没写 Cameras 时）', () => {
  const outcome = run(cameraHarness(), `
    airsimCameraCatalog = { by_vehicle: {}, default: ["0", "1", "2", "3", "4"] };
    function fakeSelect() {
      const options = [];
      return {
        options,
        value: "",
        set textContent(value) { if (value === "") options.length = 0; },
        add(option) { options.push(option); },
      };
    }
    const select = fakeSelect();
    const win = { cameraSelect: select, vehicleSelect: { value: "", options: [] }, settings: {}, viewJoinedAxis: true };
    syncCameraCameraOptions(win, "2");
    globalThis.__out = { labels: select.options.map((option) => option.textContent) };
  `);

  assert.deepEqual(Array.from(outcome.labels), ['0 · 前视', '1 · 前右', '2 · 前左', '3 · 下视', '4 · 后视']);
});

test('保存的旧相机不在清单里时，首次同步选第一台真实相机', () => {
  const outcome = run(cameraHarness(), `
    airsimCameraCatalog = { by_vehicle: {}, default: ["CameraImage"] };
    function fakeSelect() {
      const options = [];
      return {
        options,
        value: "",
        set textContent(value) { if (value === "") options.length = 0; },
        add(option) { options.push(option); },
      };
    }
    const select = fakeSelect();
    const win = { cameraSelect: select, vehicleSelect: { value: "", options: [] }, settings: { camera_name: "0" }, viewJoinedAxis: false };
    const name = syncCameraCameraOptions(win, "0");
    globalThis.__out = { name, values: select.options.map((option) => option.value) };
  `);

  assert.equal(outcome.name, 'CameraImage');
  assert.deepEqual(Array.from(outcome.values), ['CameraImage']);
});

test('感知轴指向清单外的相机时保留为 custom', () => {
  const outcome = run(cameraHarness(), `
    airsimCameraCatalog = { by_vehicle: {}, default: ["0", "1"] };
    function fakeSelect() {
      const options = [];
      return {
        options,
        value: "",
        set textContent(value) { if (value === "") options.length = 0; },
        add(option) { options.push(option); },
      };
    }
    const select = fakeSelect();
    const win = { cameraSelect: select, vehicleSelect: { value: "", options: [] }, settings: {}, viewJoinedAxis: true };
    const name = syncCameraCameraOptions(win, "RemoteCam");
    globalThis.__out = { name, labels: select.options.map((option) => option.textContent) };
  `);

  assert.equal(outcome.name, 'RemoteCam');
  assert.deepEqual(Array.from(outcome.labels), ['0 · 前视', '1 · 前右', 'RemoteCam · custom']);
});
