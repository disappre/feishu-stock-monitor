# -*- coding: utf-8 -*-
"""调度入口。

用法：
    python -m stock_monitor.main --once   # 单次全量扫描（调试）
    python -m stock_monitor.main          # 常驻：盘中轮询 + 收盘复盘
"""
from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

import yaml
from apscheduler.schedulers.blocking import BlockingScheduler
from dotenv import load_dotenv

from .data.feed import fetch_kline
from .engine.base import RuleEngine
from .llm.interpreter import interpret
from .notify import feishu_bot
from .notify.bitable import append_signal
from .utils.kline_plot import plot_kline
from .watchlist import load_watchlist

load_dotenv()
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("monitor")

BASE_DIR = Path(__file__).resolve().parent.parent
with open(BASE_DIR / "config" / "watchlist.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

engine = RuleEngine(CONFIG)


def scan_all() -> int:
    """全量扫描自选股，信号推飞书。返回信号数。"""
    count = 0
    watchlist = load_watchlist(CONFIG)  # 每轮重读：my_picks.txt 改完即生效
    for code, name in watchlist.items():
        kline = fetch_kline(code)
        if kline is None:
            logger.error("跳过 %s：行情获取失败", code)
            continue
        for signal in engine.scan(code, name, kline):
            signal.detail = interpret(signal)  # LLM 解读，失败自动回退
            try:
                # 附带迷你K线图（无应用凭证/绘图失败时自动降级为纯文本卡片）
                image_key = None
                png = plot_kline(kline, code, title=f"{name} {code}")
                if png:
                    image_key = feishu_bot.upload_image(png)
                feishu_bot.signal_to_card(signal, image_key)
                append_signal(signal)  # 多维表格流水（未配置时静默跳过）
                from .notify import trend_log
                trend_log.log_signal(signal)   # 股票走势记录表（lark-cli降级安全）
                count += 1
                logger.info("已推送: %s", signal.title)
            except Exception:
                logger.exception("推送失败: %s", signal.title)
    logger.info("本轮扫描完成，共推送 %d 条信号", count)
    return count


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="只扫描一次自选股后退出")
    parser.add_argument("--screen", action="store_true",
                        help="运行实时信号筛选：扫描股票池并推送榜单")
    parser.add_argument("--screen-local", action="store_true",
                        help="仅从 SQLite 本地库筛选，不发起任何行情请求")
    parser.add_argument("--sync-pool", action="store_true",
                        help="同步股票池已收盘日线到 SQLite（不做筛选）")
    parser.add_argument("--sublevel", action="store_true",
                        help="对自选股做30分钟次级别扫描并推送（反弹衰竭/回调企稳）")
    parser.add_argument("--workers", type=int, default=4,
                        help="全池同步的网络线程数（默认4）")
    parser.add_argument("--pool-limit", type=int, default=None,
                        help="只处理股票池前N只（调试用）")
    args = parser.parse_args()

    if args.once:
        scan_all()
        return
    if args.sync_pool:
        from .screener import load_pool
        from .sync import sync_pool
        sync_pool(load_pool(args.pool_limit), workers=args.workers)
        return
    if args.sublevel:
        from .sublevel_alert import scan_sublevel
        scan_sublevel(push=True)
        return
    if args.screen or args.screen_local:
        from .screener import run_screening
        run_screening(CONFIG, pool_limit=args.pool_limit, local_only=args.screen_local)
        return

    interval = int(CONFIG["schedule"]["intraday_interval_min"])
    close_time = CONFIG["schedule"].get("close_scan_time", "15:30")
    hh, mm = (int(x) for x in close_time.split(":"))
    noon_time = CONFIG["schedule"].get("noon_scan_time", "11:35")
    tail_time = CONFIG["schedule"].get("tail_scan_time", "14:50")
    nh, nm = (int(x) for x in noon_time.split(":"))
    th, tm = (int(x) for x in tail_time.split(":"))
    sched = BlockingScheduler(timezone="Asia/Shanghai")
    sched.add_job(scan_all, "cron", day_of_week="mon-fri",
                  hour="9-14", minute=f"*/{interval}", timezone="Asia/Shanghai")

    def intraday(session: str):
        """盘中速览（午盘/尾盘）：实时行情扫描自选股并推飞书。"""
        import subprocess, sys as _sys
        try:
            subprocess.run([_sys.executable,
                            str(Path(__file__).parents[1] / "tools" / "intraday_scan.py"),
                            "--session", session], timeout=600)
        except Exception:
            logging.getLogger("monitor").exception("盘中扫描失败(%s)", session)

    # 午盘速览（上午收盘后）
    sched.add_job(lambda: intraday("noon"), "cron", day_of_week="mon-fri",
                  hour=nh, minute=nm, timezone="Asia/Shanghai")
    # 尾盘速览（收盘前10分钟，捕捉尾盘异动）
    sched.add_job(lambda: intraday("close"), "cron", day_of_week="mon-fri",
                  hour=th, minute=tm, timezone="Asia/Shanghai")
    sched.add_job(scan_all, "cron", day_of_week="mon-fri",
                  hour=hh, minute=mm + 5, timezone="Asia/Shanghai")

    def sync_then_screen():
        from .screener import load_pool, run_screening
        from .sync import sync_pool
        sync_pool(load_pool(), workers=4)
        run_screening(CONFIG, local_only=True)
        # 走势快照写多维表格（自选股；lark-cli不可用时静默跳过）
        try:
            import subprocess, sys as _sys
            subprocess.run([_sys.executable, str(Path(__file__).parents[1] / "tools" / "log_daily_trend.py"),
                            "--chan"], timeout=600)
        except Exception:
            logging.getLogger("monitor").warning("走势快照写入跳过", exc_info=True)

    # 收盘后先落库，再纯本地扫描；不让筛选阶段依赖外部数据源。
    sched.add_job(sync_then_screen, "cron", day_of_week="mon-fri",
                  hour=hh, minute=mm + 35, timezone="Asia/Shanghai")
    sched.start()


if __name__ == "__main__":
    main()
