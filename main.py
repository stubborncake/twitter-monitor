#!/usr/bin/env python3
"""Twitter 股票推荐监听 + Bark 推送。

用法:
    python main.py              # 终端模式
    python main.py --quiet      # 后台静默模式
    python main.py --test       # 测试模式：拉一次，分析展示，发 Bark 测试
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

# ── 自动加载 .env ──────────────────────────────────────────────
ENV_PATH = Path(__file__).parent / ".env"
if ENV_PATH.exists():
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val

from fetcher import fetch_tweets, shutdown as fetcher_shutdown
from detector import ai_verify, should_trigger
from notifier import send, send_test, format_stock_alert
from storage import init_db, is_new, mark_sent, cleanup_old

CONFIG_PATH = Path(__file__).parent / "config.yaml"


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_logging(cfg: dict, quiet: bool = False) -> None:
    level = getattr(logging, cfg.get("log", {}).get("level", "INFO"))
    log_file = cfg.get("log", {}).get("file", "monitor.log")
    handlers = [logging.FileHandler(log_file, encoding="utf-8")]
    if not quiet:
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setLevel(logging.WARNING)
        handlers.append(stderr_handler)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )


def run_loop(cfg: dict, quiet: bool = False) -> None:
    bark_cfg = cfg["bark"]
    bark_cfg["device_key"] = os.environ.get("BARK_DEVICE_KEY", bark_cfg.get("device_key", ""))
    monitor_cfg = cfg["monitor"]
    ai_cfg = cfg["ai"]
    users = cfg["twitter"]["users"]

    interval = monitor_cfg.get("poll_interval_seconds", 3600)
    tweets_per_user = monitor_cfg.get("tweets_per_user", 10)
    ai_model = ai_cfg.get("model", "deepseek-chat")
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")

    if not api_key:
        logging.error("未设置 DEEPSEEK_API_KEY 环境变量，退出")
        return

    logger = logging.getLogger("main")
    logger.info(f"Twitter 监听启动，监控用户: {users}")

    while True:
        try:
            total_sent = 0

            for username in users:
                tweets = fetch_tweets(username, max_results=tweets_per_user)

                for tweet in tweets:
                    if not is_new(tweet["text"]):
                        continue

                    # AI 判断（无关键词初筛）
                    verdict = ai_verify(tweet["text"], username, api_key, ai_model)

                    if not should_trigger(verdict, ai_cfg.get("min_confidence", 0.7)):
                        continue

                    # 推送
                    action = verdict.get("action", "买入")
                    stock_name = verdict.get("stock_name", "某股")
                    stock_code = verdict.get("stock_code", "") or ""
                    original = verdict.get("original_text", tweet["text"]) or tweet["text"]
                    translation = verdict.get("translation", "") or ""

                    comment = verdict.get("comment", "") or ""

                    title, body = format_stock_alert(
                        author=username,
                        action=action,
                        stock_name=stock_name,
                        stock_code=stock_code,
                        original_text=original,
                        translation=translation,
                        comment=comment,
                    )

                    ok = send(
                        device_key=bark_cfg["device_key"],
                        title=title,
                        body=body,
                        base_url=bark_cfg.get("base_url", "https://api.day.app"),
                    )
                    if ok:
                        mark_sent(
                            tweet_id=tweet["id"],
                            content=tweet["text"],
                            author=username,
                            stock_name=stock_name,
                            stock_code=stock_code,
                            action=action,
                        )
                        total_sent += 1

            if total_sent > 0:
                cleanup_old()

            if not quiet:
                ts = datetime.now().strftime("%H:%M:%S")
                print(f"[{ts}] 轮询完成，推送 {total_sent} 次", file=sys.stderr)

        except KeyboardInterrupt:
            logger.info("用户中断，退出")
            print("\n👋 已退出")
            break
        except Exception as e:
            logger.error(f"主循环异常: {e}", exc_info=True)
            time.sleep(10)

        time.sleep(interval)


def run_test(cfg: dict) -> None:
    bark_cfg = cfg["bark"]
    bark_cfg["device_key"] = os.environ.get("BARK_DEVICE_KEY", bark_cfg.get("device_key", ""))
    monitor_cfg = cfg["monitor"]
    ai_cfg = cfg["ai"]
    users = cfg["twitter"]["users"]
    tweets_per_user = monitor_cfg.get("tweets_per_user", 10)
    ai_model = ai_cfg.get("model", "deepseek-chat")
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")

    if not api_key:
        print("❌ 未设置 DEEPSEEK_API_KEY 环境变量")
        return

    print("=" * 60)
    print("  Twitter 监听 — 测试模式")
    print("=" * 60)

    # 1. Bark 测试
    print("\n📱 发送 Bark 测试推送...")
    ok = send_test(bark_cfg["device_key"], bark_cfg.get("base_url", "https://api.day.app"))
    print(f"   {'✅ 测试推送已发送' if ok else '❌ 推送失败'}")

    # 2. 拉推文 + AI 判断
    for username in users:
        print(f"\n📡 拉取 @{username} 推文...")
        tweets = fetch_tweets(username, max_results=tweets_per_user)
        print(f"   拉取到 {len(tweets)} 条新推文")

        hit_count = 0
        for tweet in tweets:
            print(f"\n   🐦 [{tweet['created_at']}] {tweet['text'][:120]}{'...' if len(tweet['text']) > 120 else ''}")
            print(f"      🤖 AI 判断中...")
            verdict = ai_verify(tweet["text"], username, api_key, ai_model)
            if verdict:
                print(f"      结果: rec={verdict.get('is_recommendation')}, "
                      f"action={verdict.get('action')}, "
                      f"stock={verdict.get('stock_code')}, "
                      f"conf={verdict.get('confidence')}")
                if should_trigger(verdict, ai_cfg.get("min_confidence", 0.7)):
                    hit_count += 1
                    print(f"      ✅ 满足推送条件！")

                    # 测试模式也推送
                    action = verdict.get("action", "买入")
                    stock_name = verdict.get("stock_name", "某股")
                    stock_code = verdict.get("stock_code", "") or ""
                    original = verdict.get("original_text", tweet["text"]) or tweet["text"]
                    translation = verdict.get("translation", "") or ""

                    comment2 = verdict.get("comment", "") or ""

                    title, body = format_stock_alert(
                        author=username,
                        action=action,
                        stock_name=stock_name,
                        stock_code=stock_code,
                        original_text=original,
                        translation=translation,
                        comment=comment2,
                    )
                    ok = send(
                        device_key=bark_cfg["device_key"],
                        title=title,
                        body=body,
                        base_url=bark_cfg.get("base_url", "https://api.day.app"),
                    )
                    print(f"      {'📲 已推送' if ok else '❌ 推送失败'}")

                    if ok:
                        mark_sent(
                            tweet_id=tweet["id"],
                            content=tweet["text"],
                            author=username,
                            stock_name=stock_name,
                            stock_code=stock_code,
                            action=action,
                        )
                else:
                    print(f"      ❌ 不满足推送条件")
            else:
                print(f"      ❌ AI 调用失败")

        if hit_count == 0:
            print(f"   📭 @{username} 本轮无命中（这很正常，不是每条推文都是股票推荐）")

    print("\n" + "=" * 60)
    print("  测试完成")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Twitter 股票推荐监听 — Bark 推送"
    )
    parser.add_argument("--quiet", action="store_true", help="后台静默模式")
    parser.add_argument("--test", action="store_true", help="测试模式")
    args = parser.parse_args()

    cfg = load_config()
    init_db()

    try:
        if args.test:
            setup_logging(cfg, quiet=False)
            run_test(cfg)
        else:
            setup_logging(cfg, quiet=args.quiet)
            if args.quiet:
                print("🔇 Twitter 监听已启动（静默模式），按 Ctrl+C 退出")
            run_loop(cfg, quiet=args.quiet)
    finally:
        fetcher_shutdown()


if __name__ == "__main__":
    main()
