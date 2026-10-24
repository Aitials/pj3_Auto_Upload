"""GLM-4.5-Air Agent 核心，供命令行与企业微信机器人共用。"""

from __future__ import annotations

import os
from datetime import datetime
from functools import lru_cache

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

load_dotenv()

# 智谱 BigModel 提供 OpenAI 兼容接口，因此可直接复用 ChatOpenAI
BASE_URL = os.getenv("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
MODEL = os.getenv("GLM_MODEL", "glm-4.5-air")

_ALLOWED_CHARS = "0123456789+-*/().e "


@tool
def get_current_time() -> str:
    """获取当前的日期和时间，格式 年-月-日 时:分:秒。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@tool
def calculator(expression: str) -> str:
    """计算数学表达式，例如 '1024 * 8' 或 '(365 + 1) / 2'。支持加减乘除与括号。"""
    expr = expression.strip()
    if not expr or any(c not in _ALLOWED_CHARS for c in expr):
        return f"非法表达式: {expression}"
    try:
        return str(eval(expr, {"__builtins__": {}}))  # noqa: S307 - 已做字符白名单校验
    except ZeroDivisionError:
        return "错误：除数为零"
    except Exception as exc:  # noqa: BLE001
        return f"计算失败: {exc}"


def build_llm() -> ChatOpenAI:
    api_key = os.getenv("ZHIPU_API_KEY")
    if not api_key:
        raise RuntimeError("缺少 ZHIPU_API_KEY，请在 .env 中配置后重试")

    return ChatOpenAI(
        model=MODEL,
        base_url=BASE_URL,
        api_key=api_key,
        temperature=0.3,
        timeout=60,
        max_retries=2,
    )


@lru_cache(maxsize=1)
def get_agent():
    return create_agent(
        build_llm(),
        tools=[calculator, get_current_time],
        system_prompt="你是一个简洁高效的助手。能直接回答的就直答，需要实时数据或计算时才调用工具。",
    )


def ask(question: str) -> str:
    """向 Agent 提一个问题并返回最终文本回复。"""
    result = get_agent().invoke({"messages": [{"role": "user", "content": question}]})
    return result["messages"][-1].content


async def astream_answer(question: str):
    """流式产出 Agent 最终回答的文本增量，供聊天渠道做打字机效果。"""
    async for event in get_agent().astream_events(
        {"messages": [{"role": "user", "content": question}]}, version="v2"
    ):
        if event["event"] != "on_chat_model_stream":
            continue
        text = event["data"]["chunk"].content
        if isinstance(text, str) and text:
            yield text
