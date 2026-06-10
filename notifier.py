"""Bark 推送通知模块 — 复用自 jin10-monitor，新增买入/卖出 + 原文翻译。"""

import logging

import httpx

logger = logging.getLogger(__name__)

DEFAULT_LEVEL = "timeSensitive"
DEFAULT_SOUND = "bell"


def send(
    device_key: str,
    title: str,
    body: str,
    base_url: str = "https://api.day.app",
    group: str = "Twitter 监听",
    url: str = "",
    sound: str = DEFAULT_SOUND,
    level: str = DEFAULT_LEVEL,
) -> bool:
    endpoint = f"{base_url}/push"
    payload = {
        "device_key": device_key,
        "title": title,
        "body": body,
        "group": group,
        "sound": sound,
        "level": level,
        "isArchive": 1,
    }
    if url:
        payload["url"] = url

    try:
        resp = httpx.post(endpoint, json=payload, timeout=10.0)
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") == 200:
            logger.info(f"Bark 推送成功: {title}")
            return True
        else:
            logger.error(f"Bark 推送失败: {result}")
            return False
    except Exception as e:
        logger.error(f"Bark 推送异常: {e}")
        return False


def send_test(device_key: str, base_url: str = "https://api.day.app") -> bool:
    return send(
        device_key=device_key,
        title="✅ Twitter 监听已启动",
        body="如果你收到这条推送，说明 Twitter 监听配置正确！",
        base_url=base_url,
        group="Twitter 监听",
    )


def format_stock_alert(
    author: str,
    action: str,
    stock_name: str,
    stock_code: str = "",
    original_text: str = "",
    translation: str = "",
    comment: str = "",
) -> tuple[str, str]:
    """格式化股票推荐推送的标题和正文。

    Args:
        author: 推荐者（Twitter 用户名）。
        action: "买入" 或 "卖出"。
        stock_name: 股票中文名。
        stock_code: 股票代码（如 AAPL、TSLA）。
        original_text: 推文原文。
        translation: 中文翻译（若原文非中文）。
        comment: AI 一句点评。

    Returns:
        (title, body) 元组。
    """
    # 标题：Mark Minervini 推荐 买入 AAPL（苹果）
    name = stock_name or stock_code or "某股"
    if stock_code and stock_name and stock_code not in stock_name:
        title = f"{author} 推荐 {action} {stock_code}（{stock_name}）"
    elif stock_code and stock_name:
        title = f"{author} 推荐 {action} {stock_name}（{stock_code}）"
    elif stock_code:
        title = f"{author} 推荐 {action} {stock_code}"
    else:
        title = f"{author} 推荐 {action} {stock_name}"

    # 正文：AI 点评 + 原文 + 翻译
    parts = []
    if comment:
        parts.append(f"💬 {comment}")
    if original_text:
        parts.append(f"原文：{original_text}")
    if translation:
        parts.append(f"中文：{translation}")

    body = "\n\n".join(parts) if parts else f"{author} 在推文中提及 {name}"
    return title, body
