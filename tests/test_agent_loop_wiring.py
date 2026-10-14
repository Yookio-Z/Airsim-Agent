"""Agent Loop 的运行时接线：签名/调用方式必须对得上。

为什么单独有这一组：`AgentBridgeMixin._format_loop_result_body` 曾经写成
"静态方法的样子"（首参是 data，函数体不用 self）却漏了 `@staticmethod`，
而两处调用都是 `self._format_loop_result_body(data)` —— 于是每次走到 Agent Loop
的"回显结果"分支就抛

    TypeError: _format_loop_result_body() takes 1 positional argument but 2 were given

整个 execute 任务在第一步出结果时崩掉，而任务状态只显示"任务处理失败"，看不出
原因。这条路径只有多步任务才会走到，平时很难发现（324 次任务里只崩了 2 次，
但每一次都是操作员在等结果的时候）。

静态签名与实例调用的这种错配，读代码看不出来、单测里也容易漏。所以这里既测
那一个方法，也扫一遍全库的同类写法。
"""

from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_loop_result_body_is_callable_the_way_it_is_called():
    """按真实调用方式调一次：签名与调用不匹配时，Agent Loop 第一次出结果就崩。

    用真实的 AgentRuntime 壳（不是假的 mixin 子类）：这个函数会转调
    `cls._result_body_fallback`，而那个 classmethod 读 `cls._RESULT_NOISE_KEYS`
    ——常量挂在 AgentRuntime 上。壳不对就测不出真正的调用链。
    """
    from _runtime_factories import agent_runtime

    runtime = agent_runtime()
    # 技能激活返回整段操作指导：正文本身就是结果，前端要能展开
    guidance = "x" * 200
    assert runtime._format_loop_result_body({"guidance": guidance, "message": "摘要"}) == guidance
    # 普通消息
    assert runtime._format_loop_result_body({"message": "感知服务运行中"}) == "感知服务运行中"
    # 带 tool_results 的复合结果（会走到 _result_body_fallback 分支）
    composed = runtime._format_loop_result_body(
        {"message": "已执行", "tool_results": [{"tool": "drone_takeoff", "ok": True, "data": {"message": "到位"}}]}
    )
    # tool_results 分支的契约：逐个工具压成 "工具 ok/failed: 说明"，再接回退正文
    assert "drone_takeoff ok: 到位" in composed
    assert composed.strip()
    assert runtime._format_loop_result_body(None) == ""
    assert runtime._format_loop_result_body("not a dict") == ""


def test_no_method_looks_static_but_is_called_as_an_instance_method():
    """全库扫描：类里的方法若首参不是 self/cls，就必须显式声明成静态/类方法。

    这是上面那个 bug 的通用形态——写成静态的样子、却又按实例方法调用，只有
    运行到那一行才暴露。静态扫描能在提交前拦住它。
    """
    offenders: list[str] = []
    for path in (REPO_ROOT / "src").rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for item in node.body:
                if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                decorators = {ast.unparse(d) for d in item.decorator_list}
                if decorators & {"staticmethod", "classmethod"}:
                    continue
                args = [a.arg for a in item.args.posonlyargs + item.args.args]
                if args and args[0] not in {"self", "cls"}:
                    rel = path.relative_to(REPO_ROOT)
                    offenders.append(f"{rel}:{item.lineno} {node.name}.{item.name}({args[0]}, ...)")
    assert offenders == [], (
        "这些方法的首参不是 self/cls，按实例方法调用会 TypeError；"
        f"缺 @staticmethod/@classmethod：{offenders}"
    )
