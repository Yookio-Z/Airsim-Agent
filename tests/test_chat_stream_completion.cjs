// 回归：chat 回复完成后正文必须显示（2026-10-20）。
//
// 事故现场：会话文件里已经存了完整回复（例如「你好啊！有什么需要我帮忙的吗？」），
// 界面上却只有一个「思考与执行过程」折叠块，正文永远空白。根因是 message_update
// 的 payload 本身就是消息对象，而处理器按 payload.message.status 判断收尾
// （payload.message 只存在于 message_delta），于是平滑流缓冲区从不 flush；
// 短回复（<24 字）在平滑释放阈值之下，一直停在 0 字。
//
// 这里在 vm 里加载真实的 ui-core.js + ui-chat.js，只补最小 DOM 桩，直接驱动
// handleStreamEvent，验证的两条不变式：
//   1) 短回复在流式期间不提前释放（保留打字机节奏）；
//   2) 收到完成状态的 message_update 后，正文立刻是全文。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const STATIC = path.join(__dirname, '../src/ui/static');

function chatHarness() {
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
      // 不真正跑平滑循环：用例只关心缓冲区状态与 flush 时机。
      setInterval: () => 1,
      clearInterval() {},
      requestAnimationFrame: (fn) => { fn(); return 1; },
    },
    console,
    setTimeout,
    clearTimeout,
    JSON,
    Math,
    Date,
    Map,
    Set,
  });
  vm.runInContext(fs.readFileSync(path.join(STATIC, 'ui-core.js'), 'utf8'), context);
  vm.runInContext(fs.readFileSync(path.join(STATIC, 'ui-chat.js'), 'utf8'), context);
  // 渲染不是本用例的被测对象：只留状态推进与内容计算。
  vm.runInContext('scheduleChatRender = function () {};', context);
  return context;
}

function drive(context, source) {
  vm.runInContext(source, context);
  return vm.runInContext('globalThis.__outcome', context);
}

const runId = 'chat_1792482605427342400';
const fullAnswer = '你好啊！有什么需要我帮忙的吗？';

test('短回复在流式期间不提前释放，完成 update 后显示全文', () => {
  const outcome = drive(chatHarness(), `
    const running = {
      id: "m1", role: "assistant", content: "", run_id: ${JSON.stringify(runId)},
      status: "running", details: { mode: "chat" }, created_at: 1, updated_at: 1,
    };
    latestState = { messages: [running], current_run: null, llm: {}, runtime: {} };
    smoothStream.targets.clear();
    smoothStream.shown.clear();

    handleStreamEvent("message_delta", {
      id: "m1", run_id: ${JSON.stringify(runId)},
      token: ${JSON.stringify(fullAnswer)}, content: ${JSON.stringify(fullAnswer)},
      message: { ...running, content: ${JSON.stringify(fullAnswer)} },
    });
    const during = smoothShownContent(latestState.messages[0]);

    handleStreamEvent("message_update", {
      ...running,
      content: ${JSON.stringify(fullAnswer)},
      status: "complete",
      updated_at: 2,
    });
    const after = smoothShownContent(latestState.messages[0]);
    globalThis.__outcome = { during, after, buffered: smoothStream.targets.has("m1") };
  `);

  assert.equal(outcome.during, '', '短于平滑阈值的回复在流式中应保持未释放');
  assert.equal(outcome.after, fullAnswer, '完成 update 后正文必须立即可见');
  assert.equal(outcome.buffered, false, '完成 update 必须清掉该消息的平滑缓冲');
});

test('漏掉 flush 时，已结束的消息仍显示全文（收尾兜底）', () => {
  const outcome = drive(chatHarness(), `
    const running = {
      id: "m2", role: "assistant", content: "", run_id: ${JSON.stringify(runId)},
      status: "running", details: { mode: "chat" }, created_at: 1, updated_at: 1,
    };
    latestState = { messages: [running], current_run: null, llm: {}, runtime: {} };
    smoothStream.targets.clear();
    smoothStream.shown.clear();
    smoothQueueDelta({ ...running, content: ${JSON.stringify(fullAnswer)} });
    const runningText = smoothShownContent({ ...running, content: ${JSON.stringify(fullAnswer)} });
    const doneText = smoothShownContent({
      ...running,
      content: ${JSON.stringify(fullAnswer)},
      status: "complete",
    });
    globalThis.__outcome = { runningText, doneText };
  `);

  assert.equal(outcome.runningText, '', '运行中仍按平滑缓冲显示');
  assert.equal(outcome.doneText, fullAnswer, '已结束的消息不得再受平滑缓冲影响');
});
