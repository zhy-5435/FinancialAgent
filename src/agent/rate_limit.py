"""接口限流：Redis 滑动窗口 QPS + 并发信号量（单用户 Agent 长任务闸 + 全局兜底闸）

设计（对齐选定方案）：
- QPS：滑动窗口日志（ZSET 时间戳，Lua 原子判定），无窗口边界突发；默认单用户 5 次/10s
- 并发（保命机制）：/chat 与 /chat/stream 属长任务（墙钟 60s + 补救生成），单用户最多
  RATE_LIMIT_USER_CONCURRENCY 个同时进行、全局兜底 RATE_LIMIT_GLOBAL_CONCURRENCY；
  槽位用「租约 ZSET」实现——成员带到期分，崩溃/断连残留由下次进入自动回收，永不死锁
- 后端：配置 RATE_LIMIT_REDIS_URL 走 Redis（多实例共享计数、重启不丢）；留空回落
  进程内存（同语义，单机零运维）。二者实现同一接口，依赖层无感知
- 键维度：当前无登录体系，取客户端 IP（X-Forwarded-For 首值优先）；上线 JWT 后
  仅需改 _client_key() 一处切到 user_id
- SSE 适配：并发槽依赖用 yield 形态，FastAPI 在「响应体发送完毕」后才执行退出代码，
  即流式回答真正结束才释放槽位；429/503 判定全部发生在建流之前
"""
from __future__ import annotations

import asyncio
import math
import time
import uuid
from collections import deque

from fastapi import HTTPException, Request

from src.agent import config as cfg

# ---------- Redis 原子脚本（滑动窗口 QPS / 租约信号量） ----------

# KEYS[1]=窗口zkey ARGV[1]=now_ms ARGV[2]=window_ms ARGV[3]=limit ARGV[4]=member
# 返回 {允许?(1/0), 建议重试秒(*100ms 取整到 0.1s)}
_QPS_LUA = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '0', ARGV[1] - ARGV[2])
local n = redis.call('ZCARD', KEYS[1])
if n < tonumber(ARGV[3]) then
  redis.call('ZADD', KEYS[1], ARGV[1], ARGV[4])
  redis.call('PEXPIRE', KEYS[1], ARGV[2] + 1000)
  return {1, 0}
end
local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
local retry = tonumber(ARGV[2])
if oldest[2] then
  retry = (oldest[2] + ARGV[2]) - ARGV[1]
end
if retry < 100 then retry = 100 end
return {0, math.ceil(retry / 100)}
"""

# KEYS[1]=槽位zkey ARGV[1]=now_ms ARGV[2]=cap ARGV[3]=member ARGV[4]=lease_ms
# 返回 1=拿到槽 0=已满
_SLOT_LUA = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '0', ARGV[1])
local n = redis.call('ZCARD', KEYS[1])
if n < tonumber(ARGV[2]) then
  redis.call('ZADD', KEYS[1], ARGV[1] + ARGV[4], ARGV[3])
  redis.call('PEXPIRE', KEYS[1], ARGV[4] * 2)
  return 1
end
return 0
"""

_GLOBAL_BUCKET = "global"


class RedisLimiter:
    """Redis 后端：QPS 滑动窗口与并发租约均由 Lua 保证原子，多实例共享计数"""

    def __init__(self, url: str):
        import redis.asyncio as aioredis

        # protocol=2：强制 RESP2 握手，兼容老版 Redis（Windows 服务版无 HELLO 命令）
        self._client = aioredis.from_url(url, decode_responses=True, protocol=2)
        self._qps_script = self._client.register_script(_QPS_LUA)
        self._slot_script = self._client.register_script(_SLOT_LUA)

    async def check_qps(self, scope: str, key: str, limit: int, window_s: float) -> float | None:
        """通过返回 None；被限返回建议重试秒数"""
        member = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
        ok, retry_ds = await self._qps_script(
            keys=[f"rl:qps:{scope}:{key}"],
            args=[int(time.time() * 1000), int(window_s * 1000), limit, member],
        )
        if ok:
            return None
        return max(0.1, int(retry_ds) / 10)

    async def acquire(self, bucket: str, member: str, cap: int, lease_s: float) -> bool:
        return bool(await self._slot_script(
            keys=[f"rl:slot:{bucket}"],
            args=[int(time.time() * 1000), cap, member, int(lease_s * 1000)],
        ))

    async def release(self, bucket: str, member: str) -> None:
        await self._client.zrem(f"rl:slot:{bucket}", member)


class MemoryLimiter:
    """内存后端（RATE_LIMIT_REDIS_URL 留空时启用）：单进程内与 Redis 后端同语义"""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._qps: dict[str, deque[float]] = {}
        self._slots: dict[str, dict[str, float]] = {}

    async def check_qps(self, scope: str, key: str, limit: int, window_s: float) -> float | None:
        now = time.monotonic()
        async with self._lock:
            dq = self._qps.setdefault(f"{scope}|{key}", deque())
            while dq and dq[0] <= now - window_s:
                dq.popleft()
            if len(dq) < limit:
                dq.append(now)
                return None
            retry = dq[0] + window_s - now
        return max(0.1, math.ceil(retry * 10) / 10)

    async def acquire(self, bucket: str, member: str, cap: int, lease_s: float) -> bool:
        now = time.monotonic()
        async with self._lock:
            slots = self._slots.setdefault(bucket, {})
            for m, exp in [kv for kv in slots.items() if kv[1] <= now]:
                del slots[m]                       # 回收过期租约（崩溃/未释放兜底）
            if len(slots) < cap:
                slots[member] = now + lease_s
                return True
            return False

    async def release(self, bucket: str, member: str) -> None:
        async with self._lock:
            self._slots.get(bucket, {}).pop(member, None)


def _build_limiter() -> RedisLimiter | MemoryLimiter:
    url = (cfg.RATE_LIMIT_REDIS_URL or "").strip()
    if url:
        return RedisLimiter(url)
    return MemoryLimiter()


limiter = _build_limiter()


async def _check_qps_safe(scope: str, key: str) -> float | None:
    """限流组件自身故障（如 Redis 不可达）时 fail-open 放行：
    限流是保护面不是业务面，不该因它把全站堵成 500。"""
    try:
        return await limiter.check_qps(scope, key, cfg.RATE_LIMIT_QPS_MAX,
                                       cfg.RATE_LIMIT_QPS_WINDOW_SECONDS)
    except Exception:
        return None


def _client_key(request: Request) -> str:
    """限流键：现阶段取 IP；接入登录后在此单点切换为 user_id"""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _busy(exc_http: int, detail: str, retry_s: float) -> HTTPException:
    return HTTPException(status_code=exc_http, detail=detail,
                         headers={"Retry-After": str(max(1, math.ceil(retry_s)))})


def qps_guard(scope: str):
    """轻量端点依赖：仅滑动窗口 QPS（按端点成本共用一套限额）"""

    async def dep(request: Request) -> None:
        if not cfg.RATE_LIMIT_ENABLED:
            return
        retry = await _check_qps_safe(scope, _client_key(request))
        if retry is not None:
            raise _busy(429, "请求过于频繁，请稍后再试", retry)

    return dep


def agent_task_guard(scope: str):
    """长任务端点依赖（/chat、/chat/stream）：QPS + 单用户并发闸 + 全局兜底闸。

    yield 形态保证并发槽在「响应完整结束（SSE 为流关闭）」后才释放；
    判定与拒绝全部发生在建流之前。
    """

    async def dep(request: Request):
        if not cfg.RATE_LIMIT_ENABLED:
            yield
            return
        key = _client_key(request)
        retry = await _check_qps_safe(scope, key)
        if retry is not None:
            raise _busy(429, "请求过于频繁，请稍后再试", retry)

        member = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
        lease = cfg.RATE_LIMIT_SLOT_LEASE_SECONDS
        got_global = got_user = False
        try:
            try:
                got_global = await limiter.acquire(_GLOBAL_BUCKET, member,
                                                   cfg.RATE_LIMIT_GLOBAL_CONCURRENCY, lease)
                got_user = await limiter.acquire(f"user:{key}", member,
                                                 cfg.RATE_LIMIT_USER_CONCURRENCY, lease)
            except Exception:
                # 后端故障 fail-open：视同拿到槽继续执行（未实际写入的槽，release 是幂等空操作）
                got_global = got_user = True
            if not got_global:
                raise _busy(503, "服务繁忙（全局并发已满），请稍后再试", 2)
            if not got_user:
                # 用户闸未取到而全局闸已取到：finally 须释放全局闸，got_user 保持 False
                raise _busy(429, "已达单用户最大并发任务数，请等待当前回答完成后再提问", 5)
            yield
        finally:
            # 逆序释放；租约到期回收兜底进程崩溃未释放的场景。
            # release 异常不外抛（避免掩盖响应链原错误），残留槽位由租约到期自动回收
            try:
                if got_user:
                    await limiter.release(f"user:{key}", member)
                if got_global:
                    await limiter.release(_GLOBAL_BUCKET, member)
            except Exception:
                pass

    return dep
