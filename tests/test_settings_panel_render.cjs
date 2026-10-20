// 设置面板（通信链路）的渲染契约：保存/连接流程依赖的渲染函数必须存在，
// 且连接预设列表能按缓存渲染出来。
//
// 背景：2026-10-20 一次清理把 params UI 连带删掉了 ui-panel-settings.js 里的
// renderConnectionsList / renderConnectionDetail（调用点还在），设置面板一打开就
// 抛 ReferenceError，表现为「无法连接、无法保存」。这条用例锁住这类“删函数但
// 留下调用”的回归。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const SOURCE = fs.readFileSync(
  path.join(__dirname, '../src/ui/static/ui-panel-settings.js'),
  'utf8',
);

const listEl = { innerHTML: '' };

function harness() {
  // 脚本用到了大量 els.* 字段；默认返回 undefined，只给列表一个真实容器。
  const els = new Proxy({}, { get: (_t, key) => (key === 'connectionsList' ? listEl : undefined) });
  listEl.innerHTML = '';
  const ctx = vm.createContext({
    els,
    document: {
      getElementById: () => null,
      querySelector: () => null,
      querySelectorAll: () => [],
      addEventListener() {},
      body: { classList: { add() {}, remove() {}, toggle() {}, contains: () => false } },
      documentElement: { style: {} },
    },
    window: { addEventListener() {}, matchMedia: () => ({ matches: false, addEventListener() {} }) },
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
    latestState: {},
    connectionSettingsLoaded: false,
    connectionsCache: [],
    activeConnectionId: '',
    autoConnectEnabled: true,
    selectedConnectionId: '',
    vehicleInfoCache: null,
    cameraSettings: {},
    DEFAULT_CAMERA_SETTINGS: {},
    api: async () => ({}),
    post: async () => ({}),
    fetch: async () => ({ json: async () => ({}) }),
    showNotice() {},
    escapeHtml: (value) => String(value),
    refresh: async () => {},
    connectionTypeLabel: () => 'UDP MAVLink',
    connectionParamsSummary: () => 'host: 127.0.0.1',
  });
  vm.runInContext(SOURCE, ctx);
  return ctx;
}

test('保存/连接流程依赖的渲染函数都存在', () => {
  const ctx = harness();
  for (const name of [
    'persistConnectionDetailFromForm',
    'saveConnectionSettings',
    'readConnectionDetailForm',
    'activateSelectedConnection',
    'renderConnectionsList',
    'renderConnectionDetail',
  ]) {
    const kind = vm.runInContext(`typeof ${name}`, ctx);
    assert.equal(kind, 'function', `${name} 必须已定义`);
  }
});

test('连接预设列表按缓存渲染，并标出当前选中项', () => {
  const ctx = harness();
  // 顶层 let 声明在 VM 的全局词法环境里，直接在页面上下文里赋值。
  vm.runInContext(
    `
    connectionsCache = [
      { id: "c1", name: "PX4 SITL", type: "udp", params: { host: "127.0.0.1" } },
      { id: "c2", name: "JETSON", type: "udp", params: { host: "192.168.1.10" } },
    ];
    selectedConnectionId = "c2";
    renderConnectionsList();
    `,
    ctx,
  );
  const html = listEl.innerHTML;
  assert.match(html, /PX4 SITL/);
  assert.match(html, /JETSON/);
  assert.match(html, /data-connection-id="c2"/);
  assert.match(html, /connection-item active/);
});

test('参数 UI 的调用点已清理（后台端点已删除，不能再留悬空调用）', () => {
  for (const gone of [
    'loadVehicleParameters(',
    'renderVehicleParametersPanel(',
    'saveVehicleParameterFromRow(',
  ]) {
    assert.equal(SOURCE.includes(gone), false, `${gone} 不应再被调用`);
  }
});

// 2026-10-20 事故：清理 params UI 时把 ui-core.js 里的
// `let vehicleParametersCache = null;` 一起删了，但 loadConnectionSettings /
// loadVehicleInfo 仍在给它赋值 → 每次打开设置面板都弹
// 「连接设置加载失败: vehicleParametersCache is not defined」。
// 这里在真实共享作用域（ui-core.js + ui-panel-settings.js）里跑加载流程，
// 锁住"参数状态缓存必须已声明且能被填充"。
function sharedScopeHarness() {
  const context = vm.createContext({
    document: {
      getElementById: () => null,
      querySelector: () => null,
      querySelectorAll: () => [],
      addEventListener() {},
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
      requestAnimationFrame: () => 1,
    },
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
  });
  vm.runInContext(
    fs.readFileSync(path.join(__dirname, '../src/ui/static/ui-core.js'), 'utf8'),
    context,
  );
  vm.runInContext(SOURCE, context);
  return context;
}

function prepareConnectionLoad(context, vehicleInfo) {
  vm.runInContext(
    `
    notices = [];
    showNotice = (message) => notices.push(String(message));
    connectionSettingsLoaded = false;
    api = async () => ({
      connections: [{ id: "c1", name: "PX4 WSL", type: "udp", params: { host: "127.0.0.1", port: 14550 } }],
      auto_connect: true,
      active_connection_id: "c1",
      detected_mavlink_links: [],
      vehicle_info: ${JSON.stringify(vehicleInfo)},
    });
    `,
    context,
  );
}

test('打开通信链路面板时参数状态缓存已声明，不再报 ReferenceError', async () => {
  const context = sharedScopeHarness();
  prepareConnectionLoad(context, {
    backend: 'px4_mavlink',
    connected: true,
    parameters: { status: 'ready', received_count: 10, expected_count: 10 },
  });

  const result = await vm.runInContext('loadConnectionSettings(true)', context);
  const state = vm.runInContext(
    '({ notices, loaded: connectionSettingsLoaded, params: vehicleParametersCache })',
    context,
  );

  assert.equal(state.notices.length, 0, `加载连接设置不应弹出错误提示: ${state.notices.join(' | ')}`);
  assert.equal(state.loaded, true);
  assert.equal(result.connections.length, 1);
  assert.equal(state.params.status, 'ready', '参数状态必须从 vehicle_info 填入缓存');
});

test('非 PX4 后端仍给出「只支持 PX4 MAVLink」的参数状态', async () => {
  const context = sharedScopeHarness();
  prepareConnectionLoad(context, { backend: 'airsim', connected: true });

  await vm.runInContext('loadConnectionSettings(true)', context);
  const state = vm.runInContext('({ notices, params: vehicleParametersCache })', context);

  assert.equal(state.notices.length, 0, state.notices.join(' | '));
  assert.equal(state.params.status, 'error');
  assert.match(state.params.message, /PX4 MAVLink/);
});
