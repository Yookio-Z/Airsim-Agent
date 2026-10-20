// 飞行模式控件：按钮 + 暗色菜单（2026-10-20）。
//
// 旧实现是原生 <select>：暗色界面里弹出白色列表、按钮只显示被截断的首字母，
// 而且看不出这些模式是干什么的。这个用例锁住新控件的两个契约：
//   1) 非 PX4 后端（不支持模式切换）整个控件不出现；
//   2) 菜单列出白名单模式 + 中文说明，当前模式高亮。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const STATIC = path.join(__dirname, '../src/ui/static');
const INDEX_HTML = fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8');

function fakeElement() {
  return {
    hidden: true,
    disabled: false,
    title: '',
    textContent: '',
    innerHTML: '',
    dataset: {},
    attrs: {},
    setAttribute(name, value) { this.attrs[name] = String(value); },
    getAttribute(name) { return this.attrs[name]; },
    addEventListener() {},
    closest: () => null,
  };
}

function harness() {
  const byId = {
    flightModeControl: fakeElement(),
    flightModeBtn: fakeElement(),
    flightModeBtnLabel: fakeElement(),
    flightModeMenu: fakeElement(),
  };
  const context = vm.createContext({
    document: {
      getElementById: (id) => byId[id] || null,
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
      EventSource: function EventSource() {},
    },
    console,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
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
  vm.runInContext(fs.readFileSync(path.join(STATIC, 'ui-telemetry.js'), 'utf8'), context);
  return { context, byId };
}

function run(context, source) {
  vm.runInContext(source, context);
  return vm.runInContext('globalThis.__out', context);
}

test('工具栏不再使用原生 select', () => {
  assert.equal(INDEX_HTML.includes('flightModeSelect'), false);
  assert.match(INDEX_HTML, /id="flightModeBtn"/);
  assert.match(INDEX_HTML, /id="flightModeMenu"/);
  assert.match(INDEX_HTML, /data-testid="control-mode-select"/);
});

test('PX4 连接后显示当前模式缩写，展开菜单带中文说明', () => {
  const { context, byId } = harness();
  const outcome = run(context, `
    flightModeInfoLoaded = true;
    flightModeSupported = true;
    flightModeAllowed = ["POSCTL", "ALTCTL", "LOITER", "HOLD", "RTL"];
    lastKnownFlightMode = "LOITER";
    syncFlightModeControl({ mode: "LOITER" }, { connected: true, stale_connection: false });
    setFlightModeMenuOpen(true);
    globalThis.__out = {
      hidden: document.getElementById("flightModeControl").hidden,
      disabled: document.getElementById("flightModeBtn").disabled,
      label: document.getElementById("flightModeBtnLabel").textContent,
      expanded: document.getElementById("flightModeBtn").attrs["aria-expanded"],
      menuHidden: document.getElementById("flightModeMenu").hidden,
      html: document.getElementById("flightModeMenu").innerHTML,
    };
  `);

  assert.equal(outcome.hidden, false);
  assert.equal(outcome.disabled, false);
  assert.equal(outcome.label, 'LOI');
  assert.equal(outcome.expanded, 'true');
  assert.equal(outcome.menuHidden, false);
  assert.match(outcome.html, /data-flight-mode="POSCTL"/);
  assert.match(outcome.html, /姿态增稳/);
  assert.match(outcome.html, /返航/);
  assert.match(outcome.html, /AUTO \/ OFFBOARD/);
});

test('不支持模式切换的后端不显示控件', () => {
  const { context, byId } = harness();
  const outcome = run(context, `
    flightModeInfoLoaded = true;
    flightModeSupported = false;
    flightModeAllowed = [];
    syncFlightModeControl({ mode: "LOITER" }, { connected: true, stale_connection: false });
    globalThis.__out = {
      hidden: document.getElementById("flightModeControl").hidden,
      disabled: document.getElementById("flightModeBtn").disabled,
    };
  `);

  assert.equal(outcome.hidden, true);
  assert.equal(outcome.disabled, true);
});
