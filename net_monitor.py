#!/usr/bin/env python3
"""即時網路流量監控工具。

每隔固定秒數讀取各網路介面的收發位元組數，計算並顯示即時上傳/下載速率與累計流量。

優先使用 psutil（跨平台：Windows / macOS / Linux）；
若未安裝 psutil，在 Linux 上會改讀 /proc/net/dev。

用法範例：
    python net_monitor.py                  # 每秒更新，顯示所有介面
    python net_monitor.py -i 2             # 每 2 秒更新
    python net_monitor.py -n eth0 wlan0    # 只監控指定介面
    python net_monitor.py --csv log.csv    # 同時寫入 CSV 記錄
    python net_monitor.py --all            # 包含 loopback 等介面

安裝 psutil：pip install psutil
"""

import argparse
import csv
import os
import sys
import time
import unicodedata
from datetime import datetime

try:
    import psutil
except ImportError:
    psutil = None


def read_counters():
    """回傳 {介面名稱: (已接收位元組, 已傳送位元組)}。"""
    if psutil is not None:
        return {
            name: (c.bytes_recv, c.bytes_sent)
            for name, c in psutil.net_io_counters(pernic=True).items()
        }

    # 後備方案：Linux /proc/net/dev
    try:
        with open("/proc/net/dev") as f:
            lines = f.readlines()[2:]
    except OSError:
        sys.exit("找不到 psutil，且無法讀取 /proc/net/dev。請先執行：pip install psutil")

    counters = {}
    for line in lines:
        name, data = line.split(":", 1)
        fields = data.split()
        counters[name.strip()] = (int(fields[0]), int(fields[8]))
    return counters


def is_loopback(name):
    return name == "lo" or name.lower().startswith("loopback")


def pad(text, width, align="<"):
    """依顯示寬度補空白（中文字佔兩格），讓中英文混排的表格能對齊。"""
    w = sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)
    space = " " * max(width - w, 0)
    return text + space if align == "<" else space + text


def fmt_bytes(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:7.2f} {unit}"
        n /= 1024
    return f"{n:7.2f} PB"


def fmt_rate(bytes_per_sec):
    return f"{fmt_bytes(bytes_per_sec)}/s"


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def parse_args():
    p = argparse.ArgumentParser(description="即時網路流量監控")
    p.add_argument("-i", "--interval", type=float, default=1.0,
                   help="更新間隔秒數（預設 1）")
    p.add_argument("-n", "--nic", nargs="+", metavar="IFACE",
                   help="只監控指定的網路介面")
    p.add_argument("--all", action="store_true",
                   help="包含 loopback 介面")
    p.add_argument("--csv", metavar="FILE",
                   help="將每次取樣的速率附加寫入 CSV 檔")
    p.add_argument("--no-clear", action="store_true",
                   help="不清除畫面，改為逐行輸出")
    args = p.parse_args()
    if args.interval <= 0:
        p.error("--interval 必須大於 0")
    return args


def select_nics(counters, args):
    if args.nic:
        missing = [n for n in args.nic if n not in counters]
        if missing:
            sys.exit(f"找不到介面：{', '.join(missing)}；可用介面：{', '.join(counters)}")
        return list(args.nic)
    return [n for n in counters if args.all or not is_loopback(n)]


def main():
    args = parse_args()
    start = prev = read_counters()
    nics = select_nics(prev, args)
    prev_time = time.monotonic()

    csv_file = writer = None
    if args.csv:
        new_file = not os.path.exists(args.csv)
        csv_file = open(args.csv, "a", newline="", encoding="utf-8")
        writer = csv.writer(csv_file)
        if new_file:
            writer.writerow(["timestamp", "interface",
                             "download_Bps", "upload_Bps",
                             "total_recv_bytes", "total_sent_bytes"])

    peak_down = peak_up = 0.0
    try:
        while True:
            time.sleep(args.interval)
            now = read_counters()
            now_time = time.monotonic()
            elapsed = now_time - prev_time
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            rows = []
            sum_down = sum_up = 0.0
            for nic in nics:
                if nic not in now or nic not in prev:
                    continue  # 介面在監控中途消失/出現
                recv, sent = now[nic]
                p_recv, p_sent = prev[nic]
                s_recv, s_sent = start.get(nic, (recv, sent))
                # 計數器重置（例如介面重啟）時避免出現負值
                down = max(recv - p_recv, 0) / elapsed
                up = max(sent - p_sent, 0) / elapsed
                sum_down += down
                sum_up += up
                rows.append((nic, down, up,
                             max(recv - s_recv, 0), max(sent - s_sent, 0)))
                if writer:
                    writer.writerow([stamp, nic, f"{down:.0f}", f"{up:.0f}", recv, sent])
            if csv_file:
                csv_file.flush()

            peak_down = max(peak_down, sum_down)
            peak_up = max(peak_up, sum_up)

            if args.no_clear:
                print(f"[{stamp}] 下載 {fmt_rate(sum_down)}  上傳 {fmt_rate(sum_up)}")
            else:
                clear_screen()
                print(f"即時網路流量監控  {stamp}  (每 {args.interval:g} 秒更新，Ctrl+C 結束)")
                print("-" * 78)
                print(pad("介面", 16) + pad("下載速率", 18, ">") + pad("上傳速率", 18, ">")
                      + pad("本次累計下載", 16, ">") + pad("本次累計上傳", 16, ">"))
                print("-" * 78)
                for nic, down, up, tot_r, tot_s in rows:
                    print(f"{pad(nic, 16)}{fmt_rate(down):>18}{fmt_rate(up):>18}"
                          f"{fmt_bytes(tot_r):>16}{fmt_bytes(tot_s):>16}")
                print("-" * 78)
                print(f"{pad('合計', 16)}{fmt_rate(sum_down):>18}{fmt_rate(sum_up):>18}")
                print(f"{pad('峰值', 16)}{fmt_rate(peak_down):>18}{fmt_rate(peak_up):>18}")

            prev, prev_time = now, now_time
    except KeyboardInterrupt:
        print("\n已停止監控。")
    finally:
        if csv_file:
            csv_file.close()


if __name__ == "__main__":
    main()
