// 解锁/上锁按钮的方向判定：点击时发 drone_arm 还是 drone_disarm，必须跟实时遥测
// 的 armed 走。这个用例在 vm 里加载真实的 ui-core.js（只补一个最小 document 桩），
// 所以测的是线上那份实现，而不是复制出来的一份逻辑。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

function coreHarness() {
  const context = vm.createContext({
    // ui-core.js 顶部会为每个 id 调一次 document.getElementById；返回 null 即可，
    // 没有加载期代码会去解引用它们。
    document: { getElementById: () => null },
    // ui-core.js 加载期会读一次 localStorage（界面偏好），补一个内存桩
    localStorage: {
      store: {},
      getItem(key) { return Object.prototype.hasOwnProperty.call(this.store, key) ? this.store[key] : null; },
      setItem(key, value) { this.store[key] = String(value); },
      removeItem(key) { delete this.store[key]; },
    },
    console,
  });
  vm.runInContext(
    fs.readFileSync(path.join(__dirname, '../src/ui/static/ui-core.js'), 'utf8'),
    context,
  );
  return context;
}

const toggleButton = { dataset: { tool: 'drone_arm', toolArmed: 'drone_disarm' } };

test('解锁/上锁按钮按实时状态选择方向', () => {
  const { armToggleToolFor } = coreHarness();

  assert.equal(armToggleToolFor(toggleButton, true), 'drone_disarm');
  assert.equal(armToggleToolFor(toggleButton, false), 'drone_arm');
  // 遥测缺失（undefined/null）必须按未解锁处理：宁可再解锁一次，也不能在状态
  // 不明时朝"上锁"方向发指令。
  assert.equal(armToggleToolFor(toggleButton, undefined), 'drone_arm');
  assert.equal(armToggleToolFor(toggleButton, null), 'drone_arm');
  assert.equal(armToggleToolFor(toggleButton, 0), 'drone_arm');
});

test('没有配对动作的按钮保持原行为', () => {
  const { armToggleToolFor } = coreHarness();

  // 普通工具按钮（起飞、返航等）没有 data-tool-armed，armed 与否都发自己
  const takeoff = { dataset: { tool: 'drone_takeoff' } };
  assert.equal(armToggleToolFor(takeoff, true), 'drone_takeoff');
  assert.equal(armToggleToolFor(takeoff, false), 'drone_takeoff');
  // 空按钮 / 缺 dataset 不能抛异常，返回空串由调用方处理
  assert.equal(armToggleToolFor({ dataset: {} }, true), '');
  assert.equal(armToggleToolFor(null, true), '');
});


// 急停按钮同样是一个按钮两个方向：闩锁后同一次点击变成解除急停。
const estopButton = { dataset: { control: 'emergency_stop', controlLatched: 'reset_emergency' } };

test('急停按钮按闩锁状态选择方向', () => {
  const { controlActionFor } = coreHarness();

  assert.equal(controlActionFor(estopButton, true), 'reset_emergency');
  assert.equal(controlActionFor(estopButton, false), 'emergency_stop');
  // 闩锁状态未知时按未闩锁处理：宁可再触发一次急停，也不能在状态不明时误解除
  assert.equal(controlActionFor(estopButton, undefined), 'emergency_stop');
  assert.equal(controlActionFor(estopButton, null), 'emergency_stop');
});

test('没有配对动作的控制按钮保持原行为', () => {
  const { controlActionFor } = coreHarness();

  for (const action of ['hover', 'land', 'return_home']) {
    const button = { dataset: { control: action } };
    assert.equal(controlActionFor(button, true), action);
    assert.equal(controlActionFor(button, false), action);
  }
  assert.equal(controlActionFor({ dataset: {} }, true), '');
  assert.equal(controlActionFor(null, true), '');
});
