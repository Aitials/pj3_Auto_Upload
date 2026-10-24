"""命令行入口：在终端里和 GLM-4.5-Air Agent 对话。

运行方式：
    cp .env.example .env     # 填入 ZHIPU_API_KEY
    uv sync
    uv run python main.py
"""

from __future__ import annotations

from agent import ask

QUESTION = "现在几点了？顺便算一下 365 * 24 * 60 等于多少"


def main() -> None:
    print(f"User: {QUESTION}\n{'-' * 50}")
    print(f"AI: {ask(QUESTION)}")


if __name__ == "__main__":
    main()
