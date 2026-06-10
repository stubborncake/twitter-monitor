"""Twitter 推文拉取模块 — 使用系统 Chromium + subprocess。

直接调用 chromium --headless --dump-dom 获取渲染后的 HTML，
然后正则提取推文。避开 Playwright 的依赖和 asyncio 冲突。
"""

import hashlib
import html
import json
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

CHROMIUM_BIN = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", "chromium")


def _extract_tweets_from_html(page_html: str, username: str) -> list[dict]:
    """从渲染后的 HTML 中提取推文。

    Twitter 在页面中内嵌了 __NEXT_DATA__ JSON，包含初始推文数据。
    """
    tweets = []

    # 方法1：从 __NEXT_DATA__ JSON 提取（最可靠）
    match = re.search(
        r'<script[^>]*>\s*window\.__INITIAL_STATE__\s*=\s*(\{.*?\});\s*</script>',
        page_html, re.DOTALL
    )
    if not match:
        # X.com 新版用不同的变量名
        match = re.search(
            r'<script[^>]*id="__NEXT_DATA__"[^>]*type="application/json"[^>]*>(.*?)</script>',
            page_html, re.DOTALL
        )

    if match:
        try:
            data = json.loads(match.group(1))
            # 遍历 JSON 查找推文数据
            _walk_and_extract(data, username, tweets)
        except (json.JSONDecodeError, KeyError):
            pass

    # 方法2：降级——直接从 HTML DOM 提取
    if not tweets:
        tweets = _extract_from_dom(page_html, username)

    return tweets


def _walk_and_extract(obj, username: str, tweets: list, depth: int = 0):
    """递归遍历 JSON，查找 tweet 对象。"""
    if depth > 20 or len(tweets) >= 20:
        return

    if isinstance(obj, dict):
        # 检测推文对象：同时有 full_text 和 rest_id
        if "full_text" in obj and "rest_id" in obj:
            text = html.unescape(obj.get("full_text", ""))
            tid = str(obj.get("rest_id", ""))
            created_at = obj.get("created_at", "")
            if text and tid:
                tweets.append({
                    "id": tid,
                    "text": _clean_text(text),
                    "created_at": created_at or "",
                    "author": username,
                })
            return

        if "legacy" in obj and isinstance(obj["legacy"], dict):
            legacy = obj["legacy"]
            if "full_text" in legacy:
                tid = str(obj.get("rest_id", ""))
                text = html.unescape(legacy.get("full_text", ""))
                created_at = legacy.get("created_at", "")
                if text and tid:
                    tweets.append({
                        "id": tid,
                        "text": _clean_text(text),
                        "created_at": created_at or "",
                        "author": username,
                    })
                return

        for v in obj.values():
            _walk_and_extract(v, username, tweets, depth + 1)

    elif isinstance(obj, list):
        for item in obj[:50]:  # 限制遍历范围
            _walk_and_extract(item, username, tweets, depth + 1)


def _extract_from_dom(page_html: str, username: str) -> list[dict]:
    """降级方案：从 HTML DOM 中正则提取推文文本。"""
    tweets = []
    # 匹配 data-testid="tweet" 区块
    tweet_blocks = re.findall(
        r'<article[^>]*data-testid="tweet"[^>]*>(.*?)</article>',
        page_html, re.DOTALL
    )

    for block in tweet_blocks[:15]:
        # 提取推文文本
        text_match = re.search(
            r'data-testid="tweetText"[^>]*>(.*?)</div>',
            block, re.DOTALL
        )
        if not text_match:
            continue

        text = re.sub(r'<[^>]+>', '', text_match.group(1))
        text = _clean_text(text)
        if not text:
            continue

        # 提取推文 ID（从链接）
        tid_match = re.search(r'/status/(\d+)', block)
        tid = tid_match.group(1) if tid_match else _make_id(text)

        tweets.append({
            "id": tid,
            "text": text,
            "created_at": "",
            "author": username,
        })

    return tweets


def _clean_text(text: str) -> str:
    """清理文本：去 HTML 实体、合并空白。"""
    text = html.unescape(text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def fetch_tweets(username: str, max_results: int = 10) -> list[dict]:
    """拉取指定用户最新推文。

    使用 chromium --headless --dump-dom 获取渲染页面，
    从内嵌 JSON 中提取推文。
    """
    url = f"https://x.com/{username}"
    logger.info(f"正在抓取 {url} ...")

    try:
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            tmpfile = f.name

        cmd = [
            CHROMIUM_BIN,
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--disable-extensions",
            "--disable-setuid-sandbox",
            "--no-first-run",
            "--no-zygote",
            "--crash-dumps-dir=/tmp",
            f"--dump-dom",
            "--virtual-time-budget=8000",
            f"--user-data-dir=/tmp/chromium-{username}",
            url,
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=45,
            env={**os.environ, "DISPLAY": ""},
        )

        page_html = result.stdout

        # 清理临时文件
        Path(tmpfile).unlink(missing_ok=True)

        if not page_html:
            logger.error(f"Chromium 未返回内容，stderr: {result.stderr[:200]}")
            return []

        tweets = _extract_tweets_from_html(page_html, username)

        # 去重
        seen = set()
        unique = []
        for t in tweets:
            if t["id"] not in seen:
                seen.add(t["id"])
                unique.append(t)

        result_tweets = unique[:max_results]
        logger.info(f"@{username} 抓取到 {len(result_tweets)} 条推文")
        return result_tweets

    except subprocess.TimeoutExpired:
        logger.error(f"抓取 @{username} 超时")
        return []
    except Exception as e:
        logger.error(f"抓取 @{username} 失败: {e}")
        return []


def shutdown():
    """清理（subprocess 模式无需做任何事）。"""
    pass


def _make_id(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()[:16]
