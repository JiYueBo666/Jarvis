import time

from src.context.manager import ContextManager
from src.data.task import TaskState
from src.engine.executor import ToolExecutor
from src.engine.model import ModelClient
from src.guard.repetition import RepetitionDetector
from src.providers.base import complete_model
from src.providers.errors import ProviderError
from src.trace.bus import SessionEventBus

MAX_PROVIDER_RETRIES = 2  # 模型调用失败后最多重试次数

# 会话总线事件名
EV_TURN_STARTED = "turn.started"
EV_TURN_FINISHED = "turn.finished"
EV_MODEL_REQUESTED = "model.requested"
EV_TOOL_EXECUTED = "tool.executed"
EV_ERROR_OCCURRED = "error.occurred"
EV_FILE_TOUCHED = "file.touched"


class Engine:
    """纯编排。没有状态、没有初始化——只有 run()。"""

    @staticmethod
    def run(
        model_client: ModelClient,
        executor: ToolExecutor,
        ctx: ContextManager,
        bus: SessionEventBus,
        query: str,
        *,
        max_steps: int = 100,
        max_tool_steps: int = 100,
        max_new_tokens: int = 8192,
        approval_policy: str = "auto",
    ) -> str:
        """执行一轮对话，返回最终答案（非流式便利方法）。"""
        for event in Engine.run_stream(
            model_client, executor, ctx, bus, query,
            max_steps=max_steps, max_tool_steps=max_tool_steps,
            max_new_tokens=max_new_tokens, approval_policy=approval_policy,
        ):
            if event["type"] in ("final", "step_limit", "error"):
                return event.get("text") or event.get("message") or "(no answer)"
        return "(no answer)"

    @staticmethod
    def run_stream(
        model_client: ModelClient,
        executor: ToolExecutor,
        ctx: ContextManager,
        bus: SessionEventBus,
        query: str,
        *,
        max_steps: int = 100,
        max_tool_steps: int = 100,
        max_new_tokens: int = 8192,
        approval_policy: str = "auto",
    ):
        """流式编排核心，yield 进度事件供上层渲染。

        事件类型: model_requested, reasoning, tool_call, tool_result, final, step_limit, error
        """
        ctx.start_turn(query)
        record = TaskState.create(query)
        model_call_count = 0
        tool_call_count = 0
        detector = RepetitionDetector()
        bus.emit(EV_TURN_STARTED, {"task_id": record.task_id, "query": query})

        def _call_with_retry():
            """调模型，遇可重试错误按线性退避重试。"""
            last_error = None
            for attempt in range(MAX_PROVIDER_RETRIES + 1):
                try:
                    return complete_model(
                        model_client, ctx.messages,
                        max_new_tokens=max_new_tokens, tools=executor.schemas,
                    )
                except ProviderError as exc:
                    last_error = exc
                    if not exc.retryable or attempt >= MAX_PROVIDER_RETRIES:
                        continue
                    time.sleep(1 * (attempt + 1))
                except Exception as exc:
                    last_error = exc
                    break
            return _user_facing_error(last_error)

        try:
            while model_call_count < max_steps and tool_call_count < max_tool_steps:
                # ── 请求模型 ──
                bus.emit(EV_MODEL_REQUESTED, {
                    "task_id": record.task_id, "model_call_seq": model_call_count + 1,
                })
                yield {
                    "type": "trace", "event": "model_requested",
                    "seq": model_call_count + 1, "attempts": record.attempts,
                    "task_id": record.task_id,
                }

                result = _call_with_retry()
                if isinstance(result, str):
                    yield {"type": "error", "message": result}
                    return

                model_call_count += 1
                record.record_attempt()
                ctx.append_assistant(result.text, result.tool_calls)

                if result.reasoning_content:
                    yield {"type": "reasoning", "content": result.reasoning_content}

                # ── 执行工具 ──
                if result.tool_calls:
                    tool_call_count += len(result.tool_calls)
                    for tc in result.tool_calls:
                        yield {
                            "type": "tool_call", "name": tc["name"],
                            "args": tc["args"], "task_id": record.task_id,
                        }

                        # 重复调用检测（短路执行）
                        warning = detector.check(tc["name"], tc["args"])
                        if warning:
                            ctx.append_tool_result(tc["id"], warning)
                            yield {
                                "type": "tool_result", "name": tc["name"],
                                "output": warning, "record": record.to_dict(),
                            }
                            bus.emit(EV_TOOL_EXECUTED, {
                                "task_id": record.task_id, "tool": tc["name"],
                                "success": False, "affected_paths": [],
                            })
                            continue

                        # 审批检查：危险工具需要用户确认
                        if executor.is_risky(tc["name"]) and approval_policy != "auto":
                            if approval_policy == "never":
                                msg = f"已拒绝危险工具 {tc['name']}（审批策略：永不通过）"
                                ctx.append_tool_result(tc["id"], msg)
                                yield {
                                    "type": "tool_result", "name": tc["name"],
                                    "output": msg, "record": record.to_dict(),
                                }
                                bus.emit(EV_TOOL_EXECUTED, {
                                    "task_id": record.task_id, "tool": tc["name"],
                                    "success": False, "affected_paths": [],
                                })
                                continue
                            # approval_policy == "ask"
                            approval = {"decision": None, "auto": False}
                            yield {
                                "type": "approval_required", "name": tc["name"],
                                "args": tc["args"], "approval": approval,
                            }
                            if approval.get("auto"):
                                approval_policy = "auto"
                            if approval["decision"] is not True:
                                msg = f"用户已拒绝工具 {tc['name']}"
                                ctx.append_tool_result(tc["id"], msg)
                                yield {
                                    "type": "tool_result", "name": tc["name"],
                                    "output": msg, "record": record.to_dict(),
                                }
                                bus.emit(EV_TOOL_EXECUTED, {
                                    "task_id": record.task_id, "tool": tc["name"],
                                    "success": False, "affected_paths": [],
                                })
                                continue

                        exec_result = executor.execute(tc["name"], tc["args"])
                        detector.record_call(tc["name"], tc["args"], exec_result.success)
                        ctx.append_tool_result(tc["id"], exec_result.output)
                        record.record_tool(tc["name"])
                        yield {
                            "type": "tool_result", "name": tc["name"],
                            "output": exec_result.output[:1000],
                            "record": record.to_dict(),
                        }

                        for p in exec_result.affected_paths:
                            record.record_touched_file(p, tc["name"])
                            bus.emit(EV_FILE_TOUCHED, {
                                "task_id": record.task_id, "path": p,
                                "operation": tc["name"],
                            })
                        bus.emit(EV_TOOL_EXECUTED, {
                            "task_id": record.task_id, "tool": tc["name"],
                            "success": exec_result.success,
                            "affected_paths": exec_result.affected_paths,
                        })
                        if not exec_result.success:
                            record.record_error(
                                tc["name"], exec_result.error_code, exec_result.output,
                            )
                            bus.emit(EV_ERROR_OCCURRED, {
                                "task_id": record.task_id, "tool": tc["name"],
                                "code": exec_result.error_code,
                            })
                    continue

                # ── 最终回答 ──
                if result.text:
                    record.finish_success(result.text)
                    bus.emit(EV_TURN_FINISHED, {
                        "task_id": record.task_id, "status": record.status,
                        "tool_steps": record.tool_steps, "model_calls": record.attempts,
                    })
                    yield {"type": "final", "text": result.text, "record": record.to_dict()}
                    return

        finally:
            ctx.finish_turn()

        # while 正常退出 → 达到步数上限
        record.stop("step_limit_reached",
                     final_answer="已用完本轮步数上限，未能完成。")
        bus.emit(EV_TURN_FINISHED, {
            "task_id": record.task_id, "status": record.status,
            "stop_reason": "step_limit_reached",
            "tool_steps": record.tool_steps, "model_calls": record.attempts,
        })
        yield {
            "type": "step_limit", "text": record.final_answer,
            "tool_steps": record.tool_steps, "model_calls": record.attempts,
            "record": record.to_dict(),
        }


def _user_facing_error(error: BaseException | None) -> str:
    """把 ProviderError 转成用户可读的错误消息。"""
    if isinstance(error, ProviderError):
        code = error.code
        if code == "auth_error":
            return "认证失败，请检查 API Key。"
        if code == "rate_limited":
            return "请求太频繁，请稍后重试。"
        if code == "timeout":
            return "请求超时，模型可能负载过高。"
        if code in ("prompt_too_long", "context_length_exceeded"):
            return "提示词过长，超过模型上下文窗口。"
        cause = error.body_excerpt or error.cause_type or error.code
        return f"模型错误 ({error.code}): {cause}"
    if error:
        return f"意外错误: {error}"
    return "模型调用失败，已重试上限。"
