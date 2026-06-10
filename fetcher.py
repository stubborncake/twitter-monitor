"""Twitter 推文拉取模块 — 使用 Playwright 无头浏览器。

不需要 Twitter API Key，通过浏览器自动化渲染 X.com 页面并提取推文。
"""

import hashlib
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Playwright 在模块级别懒加载（避免未安装时 import 报错）
_browser = None
_playwright = None


def _get_browser():
    """获取全局复用的浏览器实例（单例）。"""
    global _browser, _playwright
    if _browser is None:
        from playwright.sync_api import sync_playwright
        _playwright = sync_playwright().start()
        _browser = _playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )
        logger.info("Playwright 浏览器已启动")
    return _browser


def fetch_tweets(
    username: str,
    max_results: int = 10,
) -> list[dict]:
    """拉取指定用户的最新推文。

    Args:
        username: Twitter/X 用户名（不含 @）。
        max_results: 最多返回条数。

    Returns:
        推文列表，每条为 dict:
          {"id": str, "text": str, "created_at": str, "author": str}
    """
    tweets = []
    try:
        browser = _get_browser()
        page = browser.new_page(viewport={"width": 390, "height": 844})

        # 访问用户主页
        url = f"https://x.com/{username}"
        logger.info(f"正在加载 {url} ...")
        page.goto(url, timeout=30000, wait_until="domcontentloaded")

        # 等待推文渲染
        page.wait_for_selector('[data-testid="tweetText"]', timeout=15000)
        time.sleep(3)  # 等剩余 JS 加载完

        # 提取推文
        tweet_elements = page.query_selector_all('[data-testid="tweet"]')
        for el in tweet_elements[:max_results]:
            try:
                # 推文文本
                text_el = el.query_selector('[data-testid="tweetText"]')
                if not text_el:
                    continue
                text = text_el.inner_text().strip()
                if not text:
                    continue

                # 推文链接（含 ID）
                link_el = el.query_selector('a[href*="/status/"]')
                tweet_id = ""
                if link_el:
                    href = link_el.get_attribute("href") or ""
                    # 从 /username/status/1234567890 提取 ID
                    parts = href.split("/status/")
                    if len(parts) == 2:
                        tweet_id = parts[1].split("/")[0].split("?")[0]

                if not tweet_id:
                    tweet_id = _make_id(text)

                # 时间
                time_el = el.query_selector("time")
                created_at = ""
                if time_el:
                    created_at = time_el.get_attribute("datetime") or ""

                tweets.append({
                    "id": tweet_id,
                    "text": text,
                    "created_at": created_at,
                    "author": username,
                })

            except Exception as e:
                logger.warning(f"提取单条推文失败: {e}")
                continue

        page.close()
        logger.info(f"@{username} 抓取到 {len(tweets)} 条推文")

    except Exception as e:
        logger.error(f"抓取 @{username} 推文失败: {e}")
        # 浏览器出问题时重置，下次重连
        global _browser, _playwright
        try:
            if _browser:
                _browser.close()
        except Exception:
            pass
        _browser = None
        _playwright = None

    return tweets


def shutdown():
    """关闭浏览器（程序退出时调用）。"""
    global _browser, _playwright
    try:
        if _browser:
            _browser.close()
    except Exception:
        pass
    _browser = None
    _playwright = None


def _make_id(text: str) -> str:
    """以内容哈希作为后备 ID。"""
    return hashlib.md5(text.encode()).hexdigest()[:16]
