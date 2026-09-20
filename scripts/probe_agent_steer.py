"""执行中注入补充指令（steer）的端到端探测。

只走 UI 的 HTTP API（/api/command + /api/state），提交的指令明确禁止起飞和任何
飞行动作，用于验证：

1. 任务执行中提交新指令会作为补充指令并入当前循环（返回 status=steered），
   而不是取消任务或报"旧任务未能及时停止"；
2. 运行记录里 agent loop 的事件流出现 steer 事件，且模型后续轮次拿到了补充
   指令文本；
3. 时间线里每轮"模型思考"按轮独立，不再出现合成标签 "Call <tool>"。
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8765"
REPO = Path(__file__).resolve().parents[1]
RUNS_DIR = REPO / "src" / "data" / "task_runs"

# 这条指令带"检测 → 依据检测结果再决定下一步"的观察依赖，会走 Agent Loop
# （ReAct）路径——只有那条路径能消费补充指令；同时明确禁止任何飞行动作。
COMMAND = (
    "先检测画面里有没有车辆，确认它的特征；确认是车辆之后再稍微靠近它一点。"
    "注意不要起飞，也不要执行任何飞行动作，全程保持在地面。"
)
STEER = "补充要求：同时报告当前电池电压。"


def request(path: str, payload: dict | None = None, timeout: float = 20.0):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def main() -> None:
    state = request("/api/state")
    drone = state["tool_runtime"]["drone"]
    # agent_state.busy 同时包含"工具锁被占用"（感知轴/预览都会短暂占用），
    # 这里只关心有没有正在跑的任务。
    active = state["agent_state"].get("active_run") or {}
    assert not active.get("run_id"), f"已有任务在执行（{active.get('run_id')}），先等它结束"
    assert not drone.get("armed") and not drone.get("flying"), "飞机不在安全地面状态，终止探测"
    assert not state["supervisor"]["emergency_stop"] and not state["supervisor"]["paused"]

    submit = request("/api/command", {"command": COMMAND, "mode": "execute", "execute": True})
    print("submit:", json.dumps(submit, ensure_ascii=False)[:300], flush=True)
    run_id = str(submit.get("run_id") or "")
    if not submit.get("ok"):
        raise SystemExit("任务提交被拒绝")

    steer_resp: dict | None = None
    steering_points: list[str] = []
    started = time.monotonic()
    deadline = started + 300
    while time.monotonic() < deadline:
        time.sleep(2.0)
        state = request("/api/state")
        run = state.get("current_run") or {}
        step = str(run.get("current_step") or "")
        if steer_resp is None:
            steering_points.append(f"{run.get('phase')}/{step}")
            # 只有 Agent Loop（current_step = "loop-N"）能消费补充指令：等它真正
            # 跑起来再注入，才能验证"并入当前循环"而不是走到别的路径。
            if str(run.get("phase")) == "executing" and step.startswith("loop-"):
                steer_resp = request("/api/command", {"command": STEER, "mode": "execute", "execute": True})
                print("steer:", json.dumps(steer_resp, ensure_ascii=False)[:300], flush=True)
        else:
            record = RUNS_DIR / f"{run_id}.json"
            if record.is_file():
                try:
                    data = json.loads(record.read_text(encoding="utf-8"))
                except Exception:
                    data = {}
                if str(data.get("status")) in {"completed", "failed", "cancelled", "blocked"}:
                    break

    print(f"\n--- 收集证据 (run_id={run_id}) ---", flush=True)
    print("注入前观测到的阶段:", " | ".join(steering_points[-6:]))
    state = request("/api/state")
    events = state.get("events") or []
    steer_events = [e for e in events if str((e.get("data") or {}).get("kind") or "") == "steer"]
    replan_events = [e for e in events if str((e.get("data") or {}).get("kind") or "") == "replan"]
    joiner_events = [e for e in events if "补充指令" in str(e.get("message") or "")]
    print("steer 响应 status:", (steer_resp or {}).get("status"))
    print("steer 事件数:", len(steer_events), "| 合并提示事件:", len(joiner_events), "| replan 事件数:", len(replan_events))
    for event in joiner_events[-4:]:
        print("  event:", event.get("level"), event.get("source"), str(event.get("message"))[:120])

    record = RUNS_DIR / f"{run_id}.json"
    if record.is_file():
        data = json.loads(record.read_text(encoding="utf-8"))
        print("run status:", data.get("status"), "| phase:", data.get("phase"))
        trace = data.get("process_trace") or []
        reasoning = [item for item in trace if str(item.get("kind")) == "reasoning"]
        print(f"process_trace={len(trace)} reasoning={len(reasoning)}")
        synthetic = [item for item in reasoning if str(item.get("body") or "").strip().startswith("Call ")]
        print("合成 Call 标签条数:", len(synthetic))
        for index, item in enumerate(reasoning, 1):
            body = str(item.get("body") or "").replace("\n", " / ")
            print(f"  思考{index} [{item.get('status')}]: {body[:150]}")
        steps = ((data.get("plan") or {}).get("steps") or [])
        print("计划步骤:", [(s.get("tool"), s.get("status")) for s in steps])
    else:
        print("未找到运行记录:", record)

    messages = state.get("messages") or []
    finals = [m for m in messages if m.get("role") == "assistant" and str(m.get("run_id") or "") == run_id]
    if finals:
        print("最终回复:", str(finals[-1].get("content") or "")[:400].replace("\n", " / "))


if __name__ == "__main__":
    main()
