"""金融助手 CLI 入口：命令行多轮问答（每次独立检索，无历史会话）

启动方式（项目根目录执行，需先启动 L2 检索服务）：
    python -m src.agent.main
"""
import sys

from src.agent.graph import ask


def main():
    # Windows GBK 控制台下避免中文输出编码报错
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    print("金融助手已就绪（输入 q 退出）。回答仅基于 L1 知识库命中切片并强制溯源。")
    while True:
        try:
            question = input("\n用户问题：").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question:
            continue
        if question.lower() in ("q", "quit", "exit"):
            break
        print("\n助手回答：")
        print(ask(question))


if __name__ == "__main__":
    main()
