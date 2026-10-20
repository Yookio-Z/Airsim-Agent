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
