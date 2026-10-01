"""工具注册表与容错基建（T1.1 + M4.2）

职责：
- ToolSpec 统一声明每个工具的超时 / 重试 / 熔断 / 失败语义 / 意图域 / 信任级；
- ToolRegistry 集中注册与按候选集枚举，图节点从注册表取工具而非 import 具体函数；
- CircuitBreaker 进程内 per-tool 熔断（closed/open/half_open），连续失败打开、到期半开探活；
- run_tool 统一执行入口：硬超时（asyncio.wait_for）+ 指数退避重试 + 熔断 + 失败语义分级，
  杜绝静默吞错，失败按 hard/degradable/fallback 分别上抛或转为「工具不可用」观测供 replan。

设计约束：本模块不 import tools.py（避免循环依赖）；tools.py 在导入时把自身工具注册进来。
"""
from __future__ import annotations

import asyncio
import logging
import random
import threading
import time
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.tools import BaseTool

from src.agent.config import (
    BREAKER_FAILURE_THRESHOLD,
    BREAKER_RESET_SECONDS,
    TOOL_MAX_RETRIES,
    TOOL_RETRY_BASE_SECONDS,
)

logger = logging.getLogger(__name__)

# 失败语义：hard=失败即上抛触发拒答/降级；degradable/fallback=失败转「工具不可用」观测供循环改道
FailureClass = Literal["hard", "degradable", "fallback"]


@dataclass
class ToolSpec:
    """单个工具的注册声明。"""
    name: str
    tool: BaseTool
    intent_hint: str = ""              # 关联意图域（kb_qa/news_search/quote_query/...），供候选预过滤
    trust_level: str = "tool"          # 内容信任级：internal-kb / web / tool / user
    timeout_s: float = 30.0
    max_retries: int = TOOL_MAX_RETRIES
    backoff_base: float = TOOL_RETRY_BASE_SECONDS
    failure_class: FailureClass = "degradable"
    empty_ok: bool = True              # 空结果是否可接受（检索/网搜无命中不算失败）


@dataclass
class ToolResult:
    """一次工具执行的归一化结果。"""
    name: str
    ok: bool
    data: Any = None
    error: str | None = None
    degraded: bool = False            # True 表示因熔断/失败被降级为占位观测
    attempts: int = 1


class _CircuitBreaker:
    """极简熔断器：连续失败达阈值打开，冷却期内拒绝调用，到期转半开放行单次探活。"""

    def __init__(self, failure_threshold: int, reset_timeout: float) -> None:
        self._threshold = failure_threshold
        self._reset = reset_timeout
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at = 0.0
        self._state = "closed"  # closed / open / half_open

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def allow(self) -> bool:
        with self._lock:
            if self._state == "closed":
                return True
            if self._state == "open":
                if time.monotonic() - self._opened_at >= self._reset:
                    self._state = "half_open"  # 冷却到期，放行一次探活
                    return True
                return False
            return True  # half_open 放行探活

    def on_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._state = "closed"

    def on_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._state == "half_open" or self._failures >= self._threshold:
                self._state = "open"
                self._opened_at = time.monotonic()


class ToolRegistry:
    """工具注册表：name → ToolSpec，并持有各工具的熔断器实例。"""

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._breakers: dict[str, _CircuitBreaker] = {}

    def register(self, spec: ToolSpec) -> None:
        self._specs[spec.name] = spec
        self._breakers[spec.name] = _CircuitBreaker(
            BREAKER_FAILURE_THRESHOLD, BREAKER_RESET_SECONDS
        )

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def all_specs(self) -> list[ToolSpec]:
        return list(self._specs.values())

    def all_tools(self) -> list[BaseTool]:
        return [s.tool for s in self._specs.values()]

    def tools_for(self, names: list[str] | None) -> list[BaseTool]:
        """按候选名集合取工具；names 为空/None 表示不限制，返回全部。"""
        if not names:
            return self.all_tools()
        return [self._specs[n].tool for n in names if n in self._specs]

    def candidates_for_intent(self, intent: str | None) -> list[str] | None:
        """按意图预过滤候选工具名（缩小模型可选面，不给通用执行能力）；未知意图返回 None=全部。"""
        if not intent or intent == "agentic":
            return None
        names = [s.name for s in self._specs.values() if s.intent_hint == intent]
        return names or None


# 进程级单例：tools.py 导入时注册，graph.py / reason 节点读取
registry = ToolRegistry()


async def run_tool(spec: ToolSpec, args: dict[str, Any]) -> ToolResult:
    """带硬超时 + 指数退避重试 + 熔断 + 失败语义分级的统一工具执行入口（永不抛异常）。"""
    breaker = registry._breakers[spec.name]  # noqa: SLF001 - 同模块内部协作
    if not breaker.allow():
        return ToolResult(
            name=spec.name, ok=False, degraded=True,
            error="工具暂不可用（熔断打开，稍后自动恢复）",
        )

    last_err = ""
    total_attempts = spec.max_retries + 1
    for attempt in range(total_attempts):
        try:
            data = await asyncio.wait_for(
                spec.tool.ainvoke(args), timeout=spec.timeout_s
            )
            breaker.on_success()
            return ToolResult(name=spec.name, ok=True, data=data, attempts=attempt + 1)
        except Exception as e:  # 收敛所有异常，交由失败语义分级决定
            last_err = f"{type(e).__name__}: {e}"
            logger.warning("工具 %s 第 %d 次调用失败：%s", spec.name, attempt + 1, last_err)
            if attempt < total_attempts - 1:
                await asyncio.sleep(spec.backoff_base * (2 ** attempt) + random.random() * 0.3)

    breaker.on_failure()
    # 重试仍失败：hard 由上层据此触发拒答/降级；degradable/fallback 转占位观测供 replan
    return ToolResult(
        name=spec.name, ok=False, degraded=(spec.failure_class != "hard"),
        error=last_err, attempts=total_attempts,
    )
