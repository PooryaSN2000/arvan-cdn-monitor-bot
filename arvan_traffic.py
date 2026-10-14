#!/usr/bin/env python3
"""
ArvanCloud CDN Monitor & Reporter CLI (نسخه پیشرفته ۳.۰)
ابزار جامع خط فرمان برای مانیتورینگ مصرف ترافیک، بررسی سقف ۸ گیگ، پیش‌بینی اتمام (ETA)،
مدیریت ابر DNS و تولید اینفوگرافیک‌ها
"""

import sys
import os
import json
import argparse
from typing import List, Dict, Any, Optional

from arvan_api import (
    ArvanCDNClient,
    format_bytes,
    format_number,
    generate_progress_bar,
    DEFAULT_API_KEY,
    SUPPORTED_PERIODS,
    PERIOD_LIST
)


def render_ascii_table(headers: List[str], rows: List[List[str]], alignments: Optional[List[str]] = None) -> str:
    """رسم جدول خطی مرتب در ترمینال"""
    if not rows:
        return "داده‌ای برای نمایش وجود ندارد."

    col_count = len(headers)
    if alignments is None:
        alignments = ["<"] * col_count

    col_widths = [len(h) for h in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            if idx < col_count:
                col_widths[idx] = max(col_widths[idx], len(str(cell)))

    def format_row(values, fill=" "):
        parts = []
        for val, width, align in zip(values, col_widths, alignments):
            s = str(val)
            if align == ">":
                parts.append(s.rjust(width, fill))
            elif align == "^":
                parts.append(s.center(width, fill))
            else:
                parts.append(s.ljust(width, fill))
        return "│ " + " │ ".join(parts) + " │"

    sep_top = "┌─" + "─┬─".join("─" * w for w in col_widths) + "─┐"
    sep_mid = "├─" + "─┼─".join("─" * w for w in col_widths) + "─┤"
    sep_bot = "└─" + "─┴─".join("─" * w for w in col_widths) + "─┘"

    lines = [
        sep_top,
        format_row(headers),
        sep_mid,
    ]
    for row in rows:
        lines.append(format_row(row))
    lines.append(sep_bot)
    return "\n".join(lines)


def display_8gb_limit_report(client: ArvanCDNClient, domains: List[str], limit_gb: float = 8.0):
    print(f"\n⚡ بررسی وضعیت محدودیت رایگان ({limit_gb} گیگابایت در ۲۴ ساعت گذشته) ...")
    print("=" * 82)

    limit_items = client.check_8gb_limits(limit_gb=limit_gb, domains=domains)

    headers = ["ردیف", "نام دامنه", "ترافیک Bypass (24h)", "سقف مجاز", "درصد مصرف", "نوار پیشرفت", "وضعیت"]
    alignments = ["^", "<", ">", ">", ">", "<", "^"]
    rows = []

    for idx, item in enumerate(limit_items, 1):
        if item.get("error"):
            rows.append([
                str(idx),
                item["domain"],
                "-",
                f"{limit_gb:.1f} GB",
                "-",
                "░" * 12,
                f"❌ {item['error'][:20]}"
            ])
            continue

        pct = item["percent_used"]
        bar = generate_progress_bar(pct, length=12)
        status_display = f"{item['status_emoji']} {item['status_text']}"

        rows.append([
            str(idx),
            item["domain"],
            format_bytes(item["bypass_bytes"]),
            f"{limit_gb:.1f} GB",
            f"{pct:.1f}%",
            f"[{bar}]",
            status_display
        ])

    print(render_ascii_table(headers, rows, alignments))

    warnings = [it for it in limit_items if it.get("is_warning")]
    exceeded = [it for it in limit_items if it.get("is_exceeded")]

    if exceeded:
        print("\n🚨 دامنه‌هایی که از سقف مجاز رایگان (۸ گیگ) عبور کرده‌اند:")
        for it in exceeded:
            print(f"  • {it['domain']}: {it['bypass_gb']:.2f} GB از {limit_gb} GB ({it['percent_used']:.1f}%) ⚠️ مصرف مازاد هزینه خواهد داشت!")
    elif warnings:
        print("\n⚠️ دامنه‌هایی که در آستانه عبور از سقف مجاز هستند (بیشتر از ۷۵٪):")
        for it in warnings:
            print(f"  • {it['domain']}: {it['bypass_gb']:.2f} GB از {limit_gb} GB ({it['percent_used']:.1f}%)")
    else:
        print("\n✅ وضعیت تمام دامنه‌ها در محدوده امن و زیر سقف مجاز است.")


def display_eta_report(client: ArvanCDNClient, domains: List[str], limit_gb: float = 8.0):
    print(f"\n⏱ تحلیل سرعت مصرف لحظه‌ای (Burn Rate) و پیش‌بینی زمان اتمام سقف ({limit_gb} گیگابایت):")
    print("=" * 105)
    headers = ["ردیف", "نام دامنه", "مصرف ۲۴ساعته", "حجم باقیمانده", "سرعت مصرف (ساعتی)", "تخمین زمان اتمام سقف", "وضعیت"]
    alignments = ["^", "<", ">", ">", ">", "<", "^"]
    rows = []

    for idx, d in enumerate(domains, 1):
        info = client.calculate_burn_rate_and_eta(d, limit_gb=limit_gb)
        urgency_emoji = "🔴" if info["urgency"] == "danger" else ("🟡" if info["urgency"] == "warning" else "🟢")
        rows.append([
            str(idx),
            d,
            info["bypass_24h_readable"],
            info["remaining_readable"],
            info["hourly_burn_rate_readable"],
            info["eta_time_str"],
            f"{urgency_emoji} {info['urgency'].upper()}"
        ])

    print(render_ascii_table(headers, rows, alignments))


def display_dns_report(client: ArvanCDNClient, domains: List[str]):
    print("\n☁️ وضعیت رکوردهای DNS و ابر روشن/خاموش (پروکسی CDN آروان):")
    print("=" * 85)
    for d in domains:
        print(f"\n🌐 دامنه: {d}")
        records = client.get_dns_records(d)
        proxyable = [r for r in records if r.get("type", "").lower() in ["a", "aaaa", "cname"]]
        if not proxyable:
            print("  ⚠️ هیچ رکورد A, AAAA یا CNAME یافت نشد.")
            continue
        headers = ["نام ساب‌دامین", "نوع", "آدرس مقصد / IP", "وضعیت ابر آروان (پروکسی)"]
        alignments = ["<", "^", "<", "^"]
        rows = []
        for r in proxyable:
            val = r.get("value")
            if isinstance(val, list) and val:
                val_str = val[0].get("ip", str(val))
            else:
                val_str = str(val)
            cloud_str = "☁️ ابر روشن (فعال)" if r.get("cloud") else "🔘 ابر خاموش (غیرفعال)"
            rows.append([r.get("name", "@"), r.get("type", "").upper(), val_str, cloud_str])
        print(render_ascii_table(headers, rows, alignments))


def display_detailed_report(client: ArvanCDNClient, domains: List[str]):
    print("\n📊 دریافت آمار جامع (ترافیک + تعداد درخواست‌ها) در تمام بازه‌های زمانی ...")
    print("=" * 105)

    for domain in domains:
        print(f"\n🌐 دامنه: {domain}")
        headers = ["بازه زمانی", "ترافیک کل", "ترافیک Bypass", "درصد Bypass", "درخواست کل", "درخواست Bypass", "درصد Bypass Req"]
        alignments = ["<", ">", ">", ">", ">", ">", ">"]
        rows = []

        stats = client.get_full_domain_stats(domain)
        for p in PERIOD_LIST:
            rep = stats.get(p, {})
            if rep.get("error"):
                rows.append([SUPPORTED_PERIODS[p], "Error", "Error", "-", "Error", "Error", "-"])
                continue

            tot_b = rep["total_bytes"]
            byp_b = rep["bypass_bytes"]
            pct_b = rep["bypass_percent"]

            tot_r = rep["total_requests"]
            byp_r = rep["bypass_requests"]
            pct_r = rep["req_bypass_percent"]

            rows.append([
                SUPPORTED_PERIODS[p],
                format_bytes(tot_b),
                format_bytes(byp_b),
                f"{pct_b:.1f}%",
                format_number(tot_r),
                format_number(byp_r),
                f"{pct_r:.1f}%"
            ])

        print(render_ascii_table(headers, rows, alignments))


def display_single_period_report(client: ArvanCDNClient, domains: List[str], period: str):
    print(f"\n📊 گزارش ترافیک دامنه‌ها برای دوره: [{period}] ({SUPPORTED_PERIODS.get(period, period)}) ...")
    headers = ["ردیف", "نام دامنه", "ترافیک کل", "ترافیک کش (Saved)", "ترافیک Bypass", "درصد Bypass", "تعداد درخواست"]
    alignments = ["^", "<", ">", ">", ">", ">", ">"]
    rows = []

    total_all_bytes = 0
    total_all_bypass = 0
    total_all_reqs = 0

    for idx, domain in enumerate(domains, 1):
        rep = client.get_traffic_report(domain, period=period)
        if rep.get("error"):
            rows.append([str(idx), domain, "-", "-", "-", "-", f"❌ {rep['error'][:20]}"])
            continue

        tot = rep["total_bytes"]
        saved = rep["saved_bytes"]
        bypass = rep["bypass_bytes"]
        pct = rep["bypass_percent"]
        reqs = rep["total_requests"]

        total_all_bytes += tot
        total_all_bypass += bypass
        total_all_reqs += reqs

        rows.append([
            str(idx),
            domain,
            format_bytes(tot),
            format_bytes(saved),
            format_bytes(bypass),
            f"{pct:.1f}%",
            format_number(reqs)
        ])

    print(render_ascii_table(headers, rows, alignments))
    print(f"\n📌 مجموع کل ترافیک: {format_bytes(total_all_bytes)} | مجموع Bypass: {format_bytes(total_all_bypass)} | کل درخواست‌ها: {format_number(total_all_reqs)}")


def display_all_periods_comparison(client: ArvanCDNClient, domains: List[str]):
    print(f"\n📊 جدول مقایسه‌ای Bypass Traffic برای تمام بازه‌های زمانی ...")
    headers = ["ردیف", "دامنه"] + [f"Bypass ({p})" for p in PERIOD_LIST]
    alignments = ["^", "<"] + [">"] * len(PERIOD_LIST)
    rows = []

    for idx, domain in enumerate(domains, 1):
        row = [str(idx), domain]
        for p in PERIOD_LIST:
            rep = client.get_traffic_report(domain, period=p)
            if rep.get("error"):
                row.append("Err")
            else:
                row.append(format_bytes(rep["bypass_bytes"]))
        rows.append(row)

    print(render_ascii_table(headers, rows, alignments))


def export_charts(client: ArvanCDNClient, domains: List[str], period: str):
    print(f"\n📈 در حال تولید نمودارهای ترافیک برای بازه {period} ...")
    for d in domains:
        filename = f"chart_{d}_{period}.png"
        try:
            client.generate_chart_image(d, period=period, output_file=filename)
            print(f"  ✅ نمودار ذخیره شد: {filename}")
        except Exception as e:
            print(f"  ❌ خطا در رسم نمودار برای {d}: {e}")


def export_pie_chart(client: ArvanCDNClient, domains: List[str]):
    filename = "chart_domains_share.png"
    client.generate_pie_chart_image(domains=domains, output_file=filename)
    print(f"✅ نمودار دونات سهم دامنه‌ها ذخیره شد: {filename}")


def export_digest_card(client: ArvanCDNClient, domains: List[str], limit_gb: float = 8.0):
    filename = "digest_card.png"
    client.generate_daily_digest_card(domains=domains, limit_gb=limit_gb, output_file=filename)
    print(f"✅ پوستر اینفوگرافیک روزانه ذخیره شد: {filename}")


def main():
    parser = argparse.ArgumentParser(
        description="سامانه هوشمند مانیتورینگ CDN ابر آروان، بررسی سقف رایگان ۸ گیگ و رسم نمودارها"
    )
    parser.add_argument(
        "-k", "--api-key",
        default=os.getenv("ARVAN_API_KEY", DEFAULT_API_KEY),
        help="کلید API آروان"
    )
    parser.add_argument(
        "-l", "--check-limit",
        action="store_true",
        help="بررسی سقف مصرف رایگان ۸ گیگ در ۲۴ ساعت گذشته با نوار پیشرفت"
    )
    parser.add_argument(
        "--eta",
        action="store_true",
        help="تحلیل سرعت مصرف و پیش‌بینی زمان دقیق اتمام سقف ۸ گیگابایت"
    )
    parser.add_argument(
        "--dns",
        action="store_true",
        help="مشاهده رکوردهای DNS و وضعیت ابر روشن/خاموش"
    )
    parser.add_argument(
        "--pie",
        action="store_true",
        help="تولید نمودار دونات سهم دامنه‌ها از کل مصرف (chart_domains_share.png)"
    )
    parser.add_argument(
        "--digest",
        action="store_true",
        help="تولید پوستر گرافیکی خلاصه روزانه (digest_card.png)"
    )
    parser.add_argument(
        "--limit",
        type=float,
        default=8.0,
        help="مقدار سقف مصرف به گیگابایت (پیش‌فرض: 8.0)"
    )
    parser.add_argument(
        "-p", "--period",
        choices=PERIOD_LIST,
        default="24h",
        help="بازه زمانی گزارش (پیش‌فرض: 24h)"
    )
    parser.add_argument(
        "-d", "--domain",
        help="بررسی یک دامنه خاص به جای همه دامنه‌ها"
    )
    parser.add_argument(
        "-a", "--all-periods",
        action="store_true",
        help="نمایش جدول مقایسه‌ای Bypass برای تمام دوره‌ها (3h, 6h, 12h, 24h, 7d, 30d)"
    )
    parser.add_argument(
        "--detailed",
        action="store_true",
        help="نمایش آمار کامل ترافیک و درخواست‌ها در تمام دوره‌ها"
    )
    parser.add_argument(
        "-c", "--chart",
        action="store_true",
        help="رسم و ذخیره تصاویر نمودارهای ترافیک و درخواست‌ها (PNG)"
    )
    parser.add_argument(
        "--csv",
        metavar="FILE",
        help="ذخیره خروجی در فایل CSV"
    )
    parser.add_argument(
        "--json",
        nargs="?",
        const="",
        metavar="FILE",
        help="خروجی JSON"
    )

    args = parser.parse_args()
    client = ArvanCDNClient(api_key=args.api_key)

    print("=" * 82)
    print("🚀 سامانه مانیتورینگ مصرف ترافیک CDN ابر آروان (نسخه ۳.۰)")
    print("=" * 82)

    if args.domain:
        domains = [args.domain.strip()]
        print(f"🎯 دامنه هدف: {domains[0]}")
    else:
        print("🔍 دریافت لیست دامنه‌ها...")
        try:
            domains = client.get_domains()
        except Exception as e:
            print(f"❌ خطا در دریافت دامنه‌ها: {e}", file=sys.stderr)
            sys.exit(1)

        print(f"✅ تعداد دامنه‌های فعال: {len(domains)} ({', '.join(domains)})")

    if args.chart:
        export_charts(client, domains, args.period)
    if args.pie:
        export_pie_chart(client, domains)
    if args.digest:
        export_digest_card(client, domains, limit_gb=args.limit)

    if args.eta:
        display_eta_report(client, domains, limit_gb=args.limit)
    elif args.dns:
        display_dns_report(client, domains)
    elif args.check_limit:
        display_8gb_limit_report(client, domains, limit_gb=args.limit)
    elif args.detailed:
        display_detailed_report(client, domains)
    elif args.all_periods:
        display_all_periods_comparison(client, domains)
    elif not (args.chart or args.pie or args.digest):
        display_8gb_limit_report(client, domains, limit_gb=args.limit)
        display_single_period_report(client, domains, args.period)


if __name__ == "__main__":
    main()
