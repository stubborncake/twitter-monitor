"""AI 检测模块 — 每条推文直接丢 DeepSeek，不做关键词初筛。

推文最多 280 字符，AI 单次调用成本极低（≈¥0.001/条），
比关键词匹配精确得多，特别适合英文短文本。
"""

import json
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"

OUTPUT_SCHEMA_DESC = """
你必须严格输出以下 JSON 格式，不要输出其他内容：
{
  "is_recommendation": true或false,
  "action": "买入"或"卖出"或null,
  "stock_name": "股票中文名如苹果、特斯拉"或null,
  "stock_code": "股票代码如AAPL、TSLA"或null,
  "confidence": 0.0到1.0之间的数字,
  "comment": "一句简短点评（50字内），如：云算力赛道被严重低估，多只标的值得关注"或null,
  "original_text": "推文原文（若推文为中文则与原文相同）",
  "translation": "中文翻译（若推文为中文则填null）"
}
"""

SYSTEM_PROMPT = f"""你是一个金融推文分析器。分析输入的推文，判断发布者是否在推荐某只股票。

## 判断标准
1. **is_recommendation=true**：推文明确表达了做多/看涨/买入或做空/看跌/卖出某只或多只具体股票
2. **多股票推荐也要标记为 true**：如"强烈看好 AAPL、NVDA、TSLA" → is_recommendation=true，stock_name 和 stock_code 可用逗号分隔列出
3. **action**：
   - "买入"：做多、看涨、推荐买入、目标价上调、强烈看好
   - "卖出"：做空、看跌、建议卖出、目标价下调、强烈看空
   - null：不是推荐
4. **stock_name**：股票中文名（可逗号分隔多个）
5. **stock_code**：从推文提取的股票代码，逗号分隔（如 "AAPL, NVDA, TSLA"）
6. 模糊的、没有具体指向的不算推荐

## comment 点评
- 如果 is_recommendation=true，必须写一句 50 字内的中文点评
- 概括推荐逻辑：如"AI算力赛道被看好"、"财报超预期驱动股价"
- 如果 is_recommendation=false，comment 填 null

## 多语言处理
- 如果推文是英文或其他非中文语言：original_text 填原文，translation 填中文翻译
- 如果推文是中文：original_text 填原文，translation 填 null

## 置信度 confidence
- 0.9+: 非常明确的买卖推荐，直接点名股票
- 0.7-0.89: 可以推断但略有模糊
- <0.7: 不够明确，不推送

{OUTPUT_SCHEMA_DESC}"""


def ai_verify(
    content: str,
    author: str,
    api_key: str,
    model: str = "deepseek-chat",
) -> Optional[dict]:
    """使用 DeepSeek API 判断推文是否为股票推荐。

    Args:
        content: 推文原文。
        author: 推文作者。
        api_key: DeepSeek API Key。
        model: 模型名称。

    Returns:
        判断结果 dict，若 API 调用失败则返回 None。
    """
    user_msg = f"推文作者：@{author}\n\n推文内容：{content}"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": 512,
        "temperature": 0.0,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        resp = httpx.post(
            DEEPSEEK_API_URL,
            json=payload,
            headers=headers,
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        raw = data["choices"][0]["message"]["content"]
        result = json.loads(raw)

        logger.info(
            f"AI 判断 @{author}: rec={result.get('is_recommendation')}, "
            f"action={result.get('action')}, "
            f"stock={result.get('stock_code')}, "
            f"conf={result.get('confidence')}"
        )
        return result

    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"DeepSeek 返回格式异常: {e}")
        return None
    except Exception as e:
        logger.error(f"DeepSeek API 调用失败: {e}")
        return None


def should_trigger(result: dict, min_confidence: float = 0.7) -> bool:
    """根据 AI 判断结果，决定是否触发推送。"""
    if not result:
        return False
    return (
        result.get("is_recommendation", False)
        and result.get("action") in ("买入", "卖出")
        and (result.get("stock_name") or result.get("stock_code"))
        and result.get("confidence", 0) >= min_confidence
    )
