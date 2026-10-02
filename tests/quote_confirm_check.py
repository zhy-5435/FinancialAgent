# -*- coding: utf-8 -*-
"""「事前确认」跨公司候选 · 回归测试脚本（真实 LLM + 真实 suggest3/新浪取数）。

运行：python tests/quote_confirm_check.py   （需 .env 配置 LLM 与可访问新浪行情端点）
退出码：全通过 0，存在失败 1。非 pytest 单测（会真实联网/调用大模型），故用 __main__ 守卫。

断言四类归宿：
  1) 描述性/泛指输入（未点名，如「搞保险的老大」「做游戏的」）→ 触发 confirm，候选为 ≥2 家不同公司；
  2) 错别字/别名（点名但写错，如「贵州矛台」）→ 直接作答（ok=True，非 confirm），不被误伤成确认；
  3) 乱码（无法对应任何真实公司）→ 未识别话术；
  4) 完整 agent 链路图级短路：描述性歧义在 prepare 阶段预检后直接弹框，reason/act 与行情取数均不执行。

说明：GLM 端点即便 temperature=0 仍有一定抖动、且偶发超时，描述性用例给多次尝试（任一命中即通过）
以隔离模型不确定性；生产链路为单次调用。
"""
import sys
import time
from pathlib import Path

# 以仓库根为工作目录，保证 `src.*` 可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.agent.quote_service import get_market_quote
from src.agent.prompts import QUOTE_NOT_FOUND_ANSWER
from src.agent.graph import ask_detail

# 图级短路用例：完整 agent 链路下，描述性歧义应在调用行情工具之前弹框（steps 无 tool_result）
GRAPH_CONFIRM_CASES = [
    "那个做白酒的公司多少钱了？",
    "那个搞保险的老大现在多少钱了",
]

CONFIRM_CASES = [
    "那个搞保险的老大现在多少钱了",
    "那个做游戏的多少钱了",
    "白酒龙头现在什么价",
    "做乳制品的龙头公司股价",
]
# 错别字（点名但写错）：均能被 suggest3 快速路径纠错命中 → 稳定直接作答、不依赖 LLM、不应被误伤为确认
DIRECT_CASES = [
    "贵州矛台今天股价",
    "招商银航最新价",
    "五良液走势",
    "宁德时价现在多少钱",
]
NOTFOUND_CASES = [
    "asdkjh 多少钱",
    "xkcbmn 现在什么价",
]


def distinct_companies(cands):
    """候选显示名去重后的不同公司数（按 name 粗去重）"""
    return len({c["name"] for c in cands})


def run_confirm(q, attempts=5):
    """描述性输入：期望 confirm 且候选 ≥2 家不同公司。多次尝试隔离模型抖动。"""
    last = None
    for i in range(1, attempts + 1):
        r = get_market_quote(q, q)
        last = r
        if r.get("confirm"):
            cands = r["candidates"]
            names = [c["name"] for c in cands]
            if len(cands) >= 2 and distinct_companies(cands) >= 2:
                return True, f"attempt{i} confirm 候选={names} (guessed={r.get('guessed')})"
        elif r.get("ok"):
            last = {**r, "_note": f"attempt{i} 直接答={r['data']['name']}"}
        time.sleep(1.2)  # 重试间隔：避免快速连调触发端点限流/超时导致假阴性
    if last and last.get("confirm"):
        info = f"候选不足 2 家: {[c['name'] for c in last['candidates']]}"
    elif isinstance(last, dict) and "_note" in last:
        info = last["_note"]
    else:
        info = f"未确认: {str(last.get('markdown'))[:40]}"
    return False, f"attempts={attempts} -> {info}"


def run_direct(q):
    """错别字/别名：期望直接作答，且不被误判为确认。"""
    r = get_market_quote(q, q)
    if r.get("confirm"):
        return False, f"被误判为确认 candidates={[c['name'] for c in r['candidates']]}"
    if r.get("ok"):
        return True, f"直接作答 {r['data']['name']}({r['data']['sina_code']})"
    return False, f"未作答/未识别: {r['markdown'][:40]}"


def run_notfound(q):
    """乱码：期望未识别话术，不作答、不确认。"""
    r = get_market_quote(q, q)
    if r.get("confirm") or r.get("ok"):
        return False, f"竟然作答/确认了: {r.get('data') or r.get('candidates')}"
    return QUOTE_NOT_FOUND_ANSWER in r["markdown"], "未识别话术 OK"


def run_graph_confirm(q, attempts=3):
    """完整 agent 链路：期望 answer_type=confirm 且未执行行情工具（预检在 reason/act 之前短路）。"""
    last = None
    for i in range(1, attempts + 1):
        r = ask_detail(q)  # session_id=None → 不写库
        last = r
        steps = r.get("steps", [])
        tool_called = any(s.get("type") == "tool_result" for s in steps)
        if r.get("answer_type") == "confirm" and not tool_called:
            cands = [c["name"] for c in (r.get("confirm") or {}).get("candidates", [])]
            return True, f"attempt{i} confirm 短路(未调工具) 候选={cands}"
        time.sleep(1.2)
    at = last.get("answer_type")
    tool_called = any(s.get("type") == "tool_result" for s in last.get("steps", []))
    return False, f"attempts={attempts} -> answer_type={at!r} tool_called={tool_called}"


def main():
    passed = failed = 0

    def check(label, ok, detail):
        nonlocal passed, failed
        if ok:
            passed += 1
        else:
            failed += 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {detail}")

    print("=== 1) 描述性/泛指 → 期望 confirm（跨公司候选）===")
    for q in CONFIRM_CASES:
        check(q, *run_confirm(q))

    print("\n=== 2) 错别字/别名（点名） → 期望直接作答，不误伤为确认 ===")
    for q in DIRECT_CASES:
        check(q, *run_direct(q))

    print("\n=== 3) 乱码 → 期望未识别 ===")
    for q in NOTFOUND_CASES:
        check(q, *run_notfound(q))

    print("\n=== 4) 图级短路 → 描述性歧义在调用行情工具之前弹框（steps 无 tool_result）===")
    for q in GRAPH_CONFIRM_CASES:
        check(q, *run_graph_confirm(q))

    print(f"\n合计: PASS={passed} FAIL={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
