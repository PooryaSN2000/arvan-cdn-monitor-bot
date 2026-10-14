#!/usr/bin/env python3
"""
ArvanCloud CDN Telegram Bot (نسخه پیشرفته ۳.۰)
ربات هوشمند پایش و مدیریت ترافیک CDN ابر آروان
- پیش‌بینی زمان اتمام سقف ۸ گیگ (Burn Rate & ETA)
- تشخیص خودکار حملات و جهش ترافیک (Spike Detection)
- محاسبه هزینه ترافیک مازاد به تومان
- سوییچ ابر روشن/خاموش (Proxy Toggle) رکوردهای DNS از داخل تلگرام
- تولید اینفوگرافیک دونات سهم دامنه‌ها و پوستر خلاصه روزانه
- گزارش منابع و IPها/کشورهای پرمصرف
- سیستم احراز هویت اختصاصی ادمین
"""

import os
import sys
import time
import json
import threading
from functools import wraps
from typing import Dict, Any, List, Optional

import telebot
from telebot import types

from arvan_api import (
    ArvanCDNClient,
    format_bytes,
    format_number,
    generate_progress_bar,
    DEFAULT_API_KEY,
    SUPPORTED_PERIODS,
    PERIOD_LIST
)

CONFIG_FILE = "bot_config.json"

DEFAULT_CONFIG = {
    "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
    "admin_chat_ids": [],
    "arvan_api_key": os.getenv("ARVAN_API_KEY", DEFAULT_API_KEY),
    "limit_gb": 8.0,
    "cost_per_gb_toman": 450,
    "check_interval_minutes": 30,
    "auto_alert_enabled": True,
    "alert_threshold_percent": 75.0,
    "send_periodic_summary": True,
    "spike_alert_enabled": True
}


def load_config() -> Dict[str, Any]:
    if not os.path.exists(CONFIG_FILE):
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                if k not in cfg:
                    cfg[k] = v
            return cfg
    except Exception as e:
        print(f"Error loading {CONFIG_FILE}: {e}")
        return DEFAULT_CONFIG.copy()


def save_config(cfg: Dict[str, Any]):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def get_admin_ids() -> List[int]:
    cfg = load_config()
    raw = cfg.get("admin_chat_ids", [])
    if isinstance(raw, (int, str)):
        raw = [raw]

    for alt_key in ["admin_id", "admin_chat_id"]:
        alt_val = cfg.get(alt_key)
        if alt_val is not None:
            if isinstance(alt_val, list):
                raw.extend(alt_val)
            else:
                raw.append(alt_val)

    env_admin = os.getenv("ADMIN_CHAT_ID")
    if env_admin:
        raw.extend(env_admin.split(","))

    valid_ids = []
    for x in raw:
        try:
            s = str(x).strip()
            if s:
                valid_ids.append(int(s))
        except (ValueError, TypeError):
            pass
    return list(set(valid_ids))


def is_admin(user_id: int) -> bool:
    return int(user_id) in get_admin_ids()


config = load_config()
arvan_client = ArvanCDNClient(api_key=config.get("arvan_api_key", DEFAULT_API_KEY))

bot_token = config.get("telegram_bot_token", "").strip()
is_token_valid = bool(bot_token and bot_token != "YOUR_TELEGRAM_BOT_TOKEN" and ":" in bot_token)
bot = telebot.TeleBot(bot_token if is_token_valid else "000000000:AAEBdummyTokenPlaceholderXXXXXXXXX", parse_mode="HTML")


# ==================== دکوراتور احراز هویت ادمین ====================

def admin_only(func):
    @wraps(func)
    def wrapper(event, *args, **kwargs):
        if isinstance(event, types.CallbackQuery):
            user_id = event.from_user.id
            chat_id = event.message.chat.id
            is_callback = True
        else:
            user_id = event.from_user.id
            chat_id = event.chat.id
            is_callback = False

        admin_ids = get_admin_ids()

        if not admin_ids:
            no_admin_msg = (
                "⚠️ <b>شناسه ادمین هنوز در سیستم تنظیم نشده است!</b>\n\n"
                f"شناسه عددی (Chat ID) شما:\n"
                f"👉 <code>{user_id}</code>\n\n"
                f"💡 لطفاً فایل <code>{CONFIG_FILE}</code> را باز کرده و این شناسه را در بخش <code>admin_chat_ids</code> اضافه کنید:\n"
                f"<code>\"admin_chat_ids\": [{user_id}]</code>"
            )
            if is_callback:
                bot.answer_callback_query(event.id, text="⚠️ شناسه ادمین تنظیم نشده است!", show_alert=True)
            bot.send_message(chat_id, no_admin_msg)
            return

        if user_id not in admin_ids:
            denied_msg = (
                "⛔️ <b>دسترسی غیرمجاز!</b>\n\n"
                "این ربات خصوصی بوده و فقط به ادمین‌های مجاز پاسخ می‌دهد.\n"
                f"🆔 شناسه عددی شما (Chat ID): <code>{user_id}</code>"
            )
            if is_callback:
                bot.answer_callback_query(event.id, text="⛔️ دسترسی غیرمجاز!", show_alert=True)
            else:
                bot.send_message(chat_id, denied_msg)
            return

        return func(event, *args, **kwargs)
    return wrapper


def get_main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        types.KeyboardButton("📊 وضعیت سقف ۸ گیگ (24h)"),
        types.KeyboardButton("⏱ پیش‌بینی اتمام سقف (ETA)")
    )
    markup.add(
        types.KeyboardButton("🎨 اینفوگرافیک و نمودارها"),
        types.KeyboardButton("☁️ مدیریت ابر DNS (سوییچ)")
    )
    markup.add(
        types.KeyboardButton("🔍 منابع و ترافیک منطقه‌ای"),
        types.KeyboardButton("📑 آمار کامل دوره‌ها")
    )
    markup.add(
        types.KeyboardButton("🌐 لیست دامنه‌ها"),
        types.KeyboardButton("⚙️ وضعیت و تنظیمات")
    )
    return markup


# ==================== تولید گزارش وضعیت ====================

def format_status_message(client: ArvanCDNClient, limit_gb: float, cost_per_gb: int) -> str:
    limit_items = client.check_8gb_limits(limit_gb=limit_gb)
    if not limit_items:
        return "⚠️ هیچ دامنه‌ای در حساب کاربری یافت نشد."

    lines = [
        "🚀 <b>گزارش مصرف ترافیک CDN ابر آروان (۲۴ ساعت گذشته)</b>",
        f"🎯 سقف مجاز رایگان: <code>{limit_gb:.1f} GB</code>\n"
    ]

    tot_all_bypass = 0
    warnings = []
    exceeded = []

    for item in limit_items:
        d = item["domain"]
        if item.get("error"):
            lines.append(f"❌ <b>{d}</b>: خطا ({item['error'][:25]})\n")
            continue

        pct = item["percent_used"]
        bar = generate_progress_bar(pct, length=10)
        tot_all_bypass += item["bypass_bytes"]

        if item.get("is_exceeded"):
            exceeded.append(item)
        elif item.get("is_warning"):
            warnings.append(item)

        lines.append(
            f"{item['status_emoji']} <b>{d}</b>\n"
            f"  • ترافیک Bypass: <b>{format_bytes(item['bypass_bytes'])}</b> ({pct:.1f}%)\n"
            f"  • پیشرفت: <code>[{bar}]</code>\n"
            f"  • کل درخواست‌ها: {format_number(item.get('total_requests', 0))}\n"
        )

    lines.append("─────────────────────")
    lines.append(f"📦 <b>مجموع Bypass کل دامنه‌ها:</b> <code>{format_bytes(tot_all_bypass)}</code>")

    if exceeded:
        lines.append("\n🚨 <b>دامنه‌های عبور کرده از سقف ۸ گیگ:</b>")
        for it in exceeded:
            cost_info = client.calculate_overage_cost(it["bypass_bytes"], limit_gb=limit_gb, cost_per_gb_toman=cost_per_gb)
            lines.append(f"  ⚠️ <b>{it['domain']}</b>: {it['bypass_gb']:.2f} GB (هزینه مازاد: <b>{cost_info['cost_readable']}</b>)")
    elif warnings:
        lines.append("\n⚠️ <b>دامنه‌های در آستانه سقف (بالای ۷۵٪):</b>")
        for it in warnings:
            lines.append(f"  🔸 <b>{it['domain']}</b>: {it['bypass_gb']:.2f} GB ({it['percent_used']:.1f}%)")
    else:
        lines.append("\n✅ تمامی دامنه‌ها در محدوده امن قرار دارند.")

    return "\n".join(lines)


# ==================== هندلرهای پیام و فرامین ====================

@bot.message_handler(commands=['start', 'help'])
@admin_only
def handle_start(message):
    chat_id = message.chat.id
    welcome_text = (
        "سلام ادمین گرامی! 👋 به سامانه هوشمند مانیتورینگ CDN ابر آروان خوش آمدید.\n\n"
        "<b>قابلیت‌های پیشرفته این نسخه:</b>\n"
        "📊 <b>وضعیت سقف ۸ گیگ</b> - بررسی مصرف ۲۴ ساعته با نوار پیشرفت و محاسبه هزینه\n"
        "⏱ <b>پیش‌بینی اتمام سقف (ETA)</b> - تخمین زمان دقیق اتمام سقف بر اساس سرعت مصرف\n"
        "🎨 <b>اینفوگرافیک و نمودارها</b> - نمودار خطی، دونات سهم دامنه‌ها و پوستر روزانه\n"
        "☁️ <b>مدیریت ابر DNS</b> - سوییچ ابر روشن/خاموش رکوردهای دامنه از داخل تلگرام\n"
        "🔍 <b>منابع پرمصرف</b> - ریشه‌یابی IPها و کشورهای پرمصرف\n"
        "📑 <b>آمار کامل دوره‌ها</b> - ریز مصرف ۳ ساعته تا ۱ ماهه\n\n"
        "💡 لطفاً گزینه مورد نظر خود را از کیبورد زیر انتخاب کنید:"
    )
    bot.send_message(chat_id, welcome_text, reply_markup=get_main_keyboard())


@bot.message_handler(func=lambda msg: msg.text in ["📊 وضعیت سقف ۸ گیگ (24h)", "/status", "/now"])
@admin_only
def handle_status(message):
    chat_id = message.chat.id
    bot.send_chat_action(chat_id, "typing")
    try:
        report = format_status_message(
            arvan_client,
            config.get("limit_gb", 8.0),
            config.get("cost_per_gb_toman", 450)
        )
        bot.send_message(chat_id, report, reply_markup=get_main_keyboard())
    except Exception as e:
        bot.send_message(chat_id, f"❌ خطا در دریافت گزارش: {e}")


# ==================== ۱. پیش‌بینی زمان اتمام سقف (ETA & Burn Rate) ====================

@bot.message_handler(func=lambda msg: msg.text in ["⏱ پیش‌بینی اتمام سقف (ETA)", "/eta"])
@admin_only
def handle_eta(message):
    chat_id = message.chat.id
    bot.send_chat_action(chat_id, "typing")
    try:
        domains = arvan_client.get_domains()
        limit_gb = config.get("limit_gb", 8.0)
        cost_gb = config.get("cost_per_gb_toman", 450)

        lines = [
            "⏱ <b>تحلیل نرخ مصرف لحظه‌ای و پیش‌بینی زمان اتمام سقف:</b>",
            f"🎯 سقف مجاز: <code>{limit_gb:.1f} GB</code> در ۲۴ ساعت\n"
        ]

        for d in domains:
            eta_info = arvan_client.calculate_burn_rate_and_eta(d, limit_gb=limit_gb)
            urgency_emoji = "🔴" if eta_info["urgency"] == "danger" else ("🟡" if eta_info["urgency"] == "warning" else "🟢")

            lines.append(f"{urgency_emoji} <b>دامنه:</b> <code>{d}</code>")
            lines.append(f"  • مصرف ۲۴ ساعت: <b>{eta_info['bypass_24h_readable']}</b>")
            lines.append(f"  • حجم باقیمانده تا سقف: <b>{eta_info['remaining_readable']}</b>")
            lines.append(f"  • نرخ مصرف فعلی: <b>{eta_info['hourly_burn_rate_readable']}</b>")
            lines.append(f"  • ⏳ <b>پیش‌بینی اتمام:</b> {eta_info['eta_time_str']}")

            if eta_info["is_exceeded"]:
                cost = arvan_client.calculate_overage_cost(eta_info["bypass_24h_bytes"], limit_gb=limit_gb, cost_per_gb_toman=cost_gb)
                lines.append(f"  • 💰 هزینه تخمینی ترافیک مازاد: <b>{cost['cost_readable']}</b>")
            lines.append("")

        bot.send_message(chat_id, "\n".join(lines), reply_markup=get_main_keyboard())
    except Exception as e:
        bot.send_message(chat_id, f"❌ خطا در تحلیل پیش‌بینی: {e}")


# ==================== ۲. منوی اینفوگرافیک و نمودارها ====================

@bot.message_handler(func=lambda msg: msg.text in ["🎨 اینفوگرافیک و نمودارها", "/charts"])
@admin_only
def handle_charts_menu(message):
    chat_id = message.chat.id
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("📈 نمودار تایم‌لاین ترافیک یک دامنه", callback_data="menu_timeline_charts"),
        types.InlineKeyboardButton("🍩 نمودار دونات سهم دامنه‌ها از کل مصرف", callback_data="gen_donut_chart"),
        types.InlineKeyboardButton("🖼 پوستر اینفوگرافیک خلاصه روزانه (Daily Digest)", callback_data="gen_digest_poster")
    )
    bot.send_message(chat_id, "🎨 <b>مرکز گرافیک و تحلیل‌های بصری:</b>\nلطفاً نوع نمودار مورد نظر خود را انتخاب کنید:", reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data == "gen_donut_chart")
@admin_only
def callback_donut_chart(call):
    chat_id = call.message.chat.id
    bot.answer_callback_query(call.id, text="در حال ترسیم نمودار دونات سهم دامنه‌ها...")
    bot.send_chat_action(chat_id, "upload_photo")
    try:
        img_bytes = arvan_client.generate_pie_chart_image()
        bot.send_photo(chat_id, photo=img_bytes, caption="🍩 <b>نمودار دونات سهم هر دامنه از ترافیک ۲۴ ساعت گذشته</b>")
    except Exception as e:
        bot.send_message(chat_id, f"❌ خطا در تولید نمودار دونات: {e}")


@bot.callback_query_handler(func=lambda call: call.data == "gen_digest_poster")
@admin_only
def callback_digest_poster(call):
    chat_id = call.message.chat.id
    bot.answer_callback_query(call.id, text="در حال تولید پوستر خلاصه روزانه...")
    bot.send_chat_action(chat_id, "upload_photo")
    try:
        limit_gb = config.get("limit_gb", 8.0)
        img_bytes = arvan_client.generate_daily_digest_card(limit_gb=limit_gb)
        bot.send_photo(chat_id, photo=img_bytes, caption="🖼 <b>پوستر اینفوگرافیک روزانه وضعیت مصرف CDN دامنه‌ها</b>")
    except Exception as e:
        bot.send_message(chat_id, f"❌ خطا در تولید پوستر روزانه: {e}")


@bot.callback_query_handler(func=lambda call: call.data == "menu_timeline_charts")
@admin_only
def callback_menu_timeline(call):
    try:
        domains = arvan_client.get_domains()
        markup = types.InlineKeyboardMarkup(row_width=2)
        buttons = [types.InlineKeyboardButton(d, callback_data=f"sel_domain:{d}") for d in domains]
        markup.add(*buttons)
        bot.edit_message_text(
            "📈 لطفاً دامنه‌ای که می‌خواهید نمودار تایم‌لاین آن را دریافت کنید انتخاب نمایید:",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=markup
        )
    except Exception as e:
        bot.send_message(call.message.chat.id, f"❌ خطا: {e}")


@bot.callback_query_handler(func=lambda call: call.data.startswith("sel_domain:"))
@admin_only
def callback_select_domain(call):
    domain = call.data.split(":")[1]
    markup = types.InlineKeyboardMarkup(row_width=3)
    buttons = [
        types.InlineKeyboardButton("۳ ساعت", callback_data=f"gen_chart:{domain}:3h"),
        types.InlineKeyboardButton("۶ ساعت", callback_data=f"gen_chart:{domain}:6h"),
        types.InlineKeyboardButton("۱۲ ساعت", callback_data=f"gen_chart:{domain}:12h"),
        types.InlineKeyboardButton("۲۴ ساعت", callback_data=f"gen_chart:{domain}:24h"),
        types.InlineKeyboardButton("۱ هفته", callback_data=f"gen_chart:{domain}:7d"),
        types.InlineKeyboardButton("۱ ماه", callback_data=f"gen_chart:{domain}:30d"),
    ]
    markup.add(*buttons)
    bot.edit_message_text(
        f"📊 دامنه <b>{domain}</b> انتخاب شد.\nحالا بازه زمانی مورد نظر را انتخاب کنید:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("gen_chart:"))
@admin_only
def callback_generate_chart(call):
    _, domain, period = call.data.split(":")
    chat_id = call.message.chat.id
    bot.answer_callback_query(call.id, text="در حال رسم نمودار...")
    bot.send_chat_action(chat_id, "upload_photo")
    try:
        image_bytes = arvan_client.generate_chart_image(domain, period=period)
        caption = (
            f"📈 <b>نمودار ترافیک و درخواست‌های CDN</b>\n"
            f"🌐 دامنه: <code>{domain}</code>\n"
            f"⏱ بازه: <code>{SUPPORTED_PERIODS.get(period, period)}</code>"
        )
        bot.send_photo(chat_id, photo=image_bytes, caption=caption)
    except Exception as e:
        bot.send_message(chat_id, f"❌ خطا در تولید نمودار: {e}")


# ==================== ۳. سوییچ ابر روشن/خاموش (DNS Cloud Toggle) ====================

@bot.message_handler(func=lambda msg: msg.text in ["☁️ مدیریت ابر DNS (سوییچ)", "/dns"])
@admin_only
def handle_dns_menu(message):
    chat_id = message.chat.id
    try:
        domains = arvan_client.get_domains()
        markup = types.InlineKeyboardMarkup(row_width=2)
        buttons = [types.InlineKeyboardButton(d, callback_data=f"dns_dom:{d}") for d in domains]
        markup.add(*buttons)
        bot.send_message(chat_id, "☁️ برای مشاهده و تغییر وضعیت ابر رکوردهای DNS، دامنه مورد نظر را انتخاب کنید:", reply_markup=markup)
    except Exception as e:
        bot.send_message(chat_id, f"❌ خطا: {e}")


@bot.callback_query_handler(func=lambda call: call.data.startswith("dns_dom:"))
@admin_only
def callback_dns_domain_records(call):
    domain = call.data.split(":")[1]
    chat_id = call.message.chat.id
    bot.answer_callback_query(call.id, text="دریافت رکوردهای DNS...")
    try:
        records = arvan_client.get_dns_records(domain)
        # فیلتر رکوردهای قابل اتصال به پروکسی (A, AAAA, CNAME)
        proxyable_records = [r for r in records if r.get("type", "").lower() in ["a", "aaaa", "cname"]]

        if not proxyable_records:
            bot.send_message(chat_id, f"⚠️ هیچ رکورد قابل تغییری (A, AAAA, CNAME) در دامنه {domain} یافت نشد.")
            return

        markup = types.InlineKeyboardMarkup(row_width=1)
        for r in proxyable_records:
            r_id = r["id"]
            r_name = r.get("name", "@")
            r_type = r.get("type", "").upper()
            is_cloud = r.get("cloud", False)
            cloud_icon = "☁️ ابر روشن" if is_cloud else "🔘 ابر خاموش"
            next_state = "false" if is_cloud else "true"

            btn_text = f"{r_type} {r_name} | {cloud_icon} (تغییر)"
            callback_data = f"toggle_cloud:{domain}:{r_id}:{next_state}"
            markup.add(types.InlineKeyboardButton(btn_text, callback_data=callback_data))

        bot.send_message(
            chat_id,
            f"☁️ <b>رکوردهای DNS دامنه {domain}:</b>\nروی هر رکورد کلیک کنید تا وضعیت ابر (پروکسی CDN) آن فوراً تغییر کند:",
            reply_markup=markup
        )
    except Exception as e:
        bot.send_message(chat_id, f"❌ خطا در دریافت رکوردها: {e}")


@bot.callback_query_handler(func=lambda call: call.data.startswith("toggle_cloud:"))
@admin_only
def callback_toggle_cloud(call):
    _, domain, record_id, target_str = call.data.split(":")
    target_cloud = target_str.lower() == "true"
    chat_id = call.message.chat.id

    bot.answer_callback_query(call.id, text="در حال اعمال تغییر در سرورهای آروان...")
    try:
        ok = arvan_client.toggle_dns_cloud(domain, record_id, target_cloud)
        status_text = "روشن (پروکسی فعال) ☁️" if target_cloud else "خاموش (پروکسی غیرفعال) 🔘"
        if ok:
            bot.send_message(chat_id, f"✅ وضعیت ابر رکورد با موفقیت به <b>{status_text}</b> تغییر یافت.\n🌐 دامنه: <code>{domain}</code>")
        else:
            bot.send_message(chat_id, f"❌ تغییر وضعیت با خطا مواجه شد.")
    except Exception as e:
        bot.send_message(chat_id, f"❌ خطا در تغییر ابر DNS: {e}")


# ==================== ۴. ریشه‌یابی و منابع پرمصرف (IP / Geo) ====================

@bot.message_handler(func=lambda msg: msg.text in ["🔍 منابع و ترافیک منطقه‌ای", "/sources"])
@admin_only
def handle_sources_menu(message):
    chat_id = message.chat.id
    try:
        domains = arvan_client.get_domains()
        markup = types.InlineKeyboardMarkup(row_width=2)
        buttons = [types.InlineKeyboardButton(d, callback_data=f"src_dom:{d}") for d in domains]
        markup.add(*buttons)
        bot.send_message(chat_id, "🔍 برای مشاهده آمار مناطق و کشورهای پرمصرف، دامنه را انتخاب کنید:", reply_markup=markup)
    except Exception as e:
        bot.send_message(chat_id, f"❌ خطا: {e}")


@bot.callback_query_handler(func=lambda call: call.data.startswith("src_dom:"))
@admin_only
def callback_sources_report(call):
    domain = call.data.split(":")[1]
    chat_id = call.message.chat.id
    bot.answer_callback_query(call.id, text="دریافت اطلاعات جغرافیایی و منابع...")
    try:
        sources = arvan_client.get_top_traffic_sources(domain, period="24h")
        stype = sources.get("type")
        data = sources.get("data", [])

        if not data:
            bot.send_message(chat_id, f"⚠️ داده‌ای برای منابع مصرف دامنه {domain} ثبت نشده است.")
            return

        lines = [f"🔍 <b>تحلیل منابع مصرف ترافیک (۲۴ ساعت) برای {domain}:</b>\n"]
        if stype == "ips":
            lines.append("🌐 <b>۱۰ آدرس IP با بیشترین حجم درخواست:</b>")
            for idx, ip_item in enumerate(data, 1):
                ip = ip_item.get("ip", "Unknown")
                cnt = format_number(ip_item.get("count", 0))
                country = ip_item.get("country", "-")
                lines.append(f"{idx}. <code>{ip}</code> ({country}): <b>{cnt}</b> درخواست")
        else:
            lines.append("🗺 <b>مناطق و کشورهای پرمصرف بر اساس ترافیک:</b>")
            for idx, geo_item in enumerate(data, 1):
                country = geo_item.get("country") or geo_item.get("name") or "ناشناس"
                traff = format_bytes(geo_item.get("traffics") or geo_item.get("value") or 0)
                reqs = format_number(geo_item.get("requests") or 0)
                lines.append(f"{idx}. <b>{country}</b>: {traff} (تعداد درخواست: {reqs})")

        bot.send_message(chat_id, "\n".join(lines))
    except Exception as e:
        bot.send_message(chat_id, f"❌ خطا: {e}")


# ==================== سایر منوها و دستورات ====================

@bot.message_handler(func=lambda msg: msg.text in ["🌐 لیست دامنه‌ها", "/domains"])
@admin_only
def handle_domains(message):
    chat_id = message.chat.id
    bot.send_chat_action(chat_id, "typing")
    try:
        domains = arvan_client.get_domains()
        text = f"🌐 <b>دامنه‌های فعال در حساب آروان ({len(domains)} دامنه):</b>\n\n"
        for idx, d in enumerate(domains, 1):
            text += f"{idx}. <code>{d}</code>\n"
        bot.send_message(chat_id, text, reply_markup=get_main_keyboard())
    except Exception as e:
        bot.send_message(chat_id, f"❌ خطا: {e}")


@bot.message_handler(func=lambda msg: msg.text in ["📑 آمار کامل دوره‌ها", "/details"])
@admin_only
def handle_details_menu(message):
    chat_id = message.chat.id
    try:
        domains = arvan_client.get_domains()
        markup = types.InlineKeyboardMarkup(row_width=2)
        buttons = [types.InlineKeyboardButton(d, callback_data=f"view_details:{d}") for d in domains]
        markup.add(*buttons)
        bot.send_message(chat_id, "📑 برای مشاهده آمار جامع (۳ ساعته تا ۱ ماهه)، دامنه را انتخاب کنید:", reply_markup=markup)
    except Exception as e:
        bot.send_message(chat_id, f"❌ خطا: {e}")


@bot.callback_query_handler(func=lambda call: call.data.startswith("view_details:"))
@admin_only
def callback_view_details(call):
    domain = call.data.split(":")[1]
    chat_id = call.message.chat.id
    bot.answer_callback_query(call.id, text="دریافت آمار...")
    bot.send_chat_action(chat_id, "typing")
    try:
        stats = arvan_client.get_full_domain_stats(domain)
        text_lines = [f"📑 <b>آمار تفکیکی تمام دوره‌ها برای دامنه:</b> <code>{domain}</code>\n"]
        for p in PERIOD_LIST:
            rep = stats.get(p, {})
            if rep.get("error"):
                text_lines.append(f"⏱ <b>{SUPPORTED_PERIODS[p]}</b>: خطا")
                continue
            tot_b = rep["total_bytes"]
            byp_b = rep["bypass_bytes"]
            pct_b = rep["bypass_percent"]
            tot_r = rep["total_requests"]
            byp_r = rep["bypass_requests"]
            text_lines.append(
                f"⏱ <b>{SUPPORTED_PERIODS[p]}</b>:\n"
                f"  • ترافیک Bypass: <b>{format_bytes(byp_b)}</b> ({pct_b:.1f}% از کل {format_bytes(tot_b)})\n"
                f"  • درخواست‌ها: <b>{format_number(byp_r)}</b> Bypass (از کل {format_number(tot_r)})\n"
            )
        bot.send_message(chat_id, "\n".join(text_lines))
    except Exception as e:
        bot.send_message(chat_id, f"❌ خطا در استخراج آمار: {e}")


@bot.message_handler(func=lambda msg: msg.text in ["⚙️ وضعیت و تنظیمات", "/settings"])
@admin_only
def handle_settings(message):
    chat_id = message.chat.id
    admins = get_admin_ids()
    interval = config.get("check_interval_minutes", 30)
    limit = config.get("limit_gb", 8.0)
    cost = config.get("cost_per_gb_toman", 450)
    auto_alert = "فعال ✅" if config.get("auto_alert_enabled", True) else "غیرفعال ❌"
    spike_alert = "فعال ✅" if config.get("spike_alert_enabled", True) else "غیرفعال ❌"
    summary = "فعال ✅" if config.get("send_periodic_summary", True) else "غیرفعال ❌"

    admins_str = ", ".join(f"<code>{a}</code>" for a in admins) if admins else "تنظیم نشده"

    text = (
        "⚙️ <b>تنظیمات پیشرفته سامانه مانیتورینگ:</b>\n\n"
        f"👑 <b>شناسه‌های ادمین:</b> {admins_str}\n"
        f"⏱ <b>فاصله زمانی بررسی:</b> هر {interval} دقیقه یکبار\n"
        f"🎯 <b>سقف محدودیت رایگان:</b> {limit} گیگابایت در ۲۴ ساعت\n"
        f"💰 <b>تعرفه هر گیگ مازاد:</b> {cost} تومان\n"
        f"🔔 <b>هشدار عبور از سقف:</b> {auto_alert}\n"
        f"⚡️ <b>تشخیص جهش ناگهانی (Spike):</b> {spike_alert}\n"
        f"📋 <b>ارسال خلاصه وضعیت دوره‌ای:</b> {summary}\n\n"
        "<b>دستورات مدیریتی ادمین:</b>\n"
        "• <code>/addadmin 123456789</code> - اضافه کردن ادمین\n"
        "• <code>/deladmin 123456789</code> - حذف ادمین\n"
        "• <code>/admins</code> - مشاهده لیست ادمین‌ها\n"
        "• <code>/setlimit 8.0</code> - تغییر سقف مجاز (گیگابایت)\n"
        "• <code>/setcost 450</code> - تنظیم تعرفه هر گیگ به تومان\n"
        "• <code>/interval 30</code> - تنظیم فاصله بررسی (دقیقه)\n"
        "• <code>/toggle_alert</code> - روشن/خاموش کردن هشدار سقف\n"
        "• <code>/toggle_spike</code> - روشن/خاموش کردن هشدار جهش ناگهانی"
    )
    bot.send_message(chat_id, text, reply_markup=get_main_keyboard())


@bot.message_handler(commands=['admins'])
@admin_only
def handle_list_admins(message):
    admins = get_admin_ids()
    text = "👑 <b>لیست ادمین‌های مجاز ربات:</b>\n\n" + "\n".join(f"{i}. <code>{a}</code>" for i, a in enumerate(admins, 1))
    bot.reply_to(message, text)


@bot.message_handler(commands=['addadmin'])
@admin_only
def handle_add_admin(message):
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "فرمت دستور: <code>/addadmin 123456789</code>")
        return
    try:
        new_id = int(parts[1])
        cfg = load_config()
        current_admins = get_admin_ids()
        if new_id in current_admins:
            bot.reply_to(message, f"این شناسه از قبل در لیست ادمین‌ها قرار دارد.")
            return
        current_admins.append(new_id)
        cfg["admin_chat_ids"] = current_admins
        save_config(cfg)
        bot.reply_to(message, f"✅ شناسه <code>{new_id}</code> به لیست ادمین‌ها اضافه شد.")
    except ValueError:
        bot.reply_to(message, "❌ لطفاً شناسه عددی معتبر وارد کنید.")


@bot.message_handler(commands=['deladmin'])
@admin_only
def handle_del_admin(message):
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "فرمت دستور: <code>/deladmin 123456789</code>")
        return
    try:
        rem_id = int(parts[1])
        cfg = load_config()
        current_admins = get_admin_ids()
        if rem_id not in current_admins:
            bot.reply_to(message, "این شناسه در لیست ادمین‌ها یافت نشد.")
            return
        current_admins.remove(rem_id)
        cfg["admin_chat_ids"] = current_admins
        save_config(cfg)
        bot.reply_to(message, f"✅ شناسه <code>{rem_id}</code> از لیست ادمین‌ها حذف شد.")
    except ValueError:
        bot.reply_to(message, "❌ لطفاً شناسه عددی معتبر وارد کنید.")


@bot.message_handler(commands=['setlimit'])
@admin_only
def handle_setlimit(message):
    try:
        parts = message.text.split()
        val = float(parts[1])
        config["limit_gb"] = val
        save_config(config)
        bot.reply_to(message, f"✅ سقف محدودیت روی {val:.1f} گیگابایت تنظیم شد.")
    except Exception:
        bot.reply_to(message, "فرمت دستور: <code>/setlimit 8.0</code>")


@bot.message_handler(commands=['setcost'])
@admin_only
def handle_setcost(message):
    try:
        parts = message.text.split()
        val = int(parts[1])
        config["cost_per_gb_toman"] = val
        save_config(config)
        bot.reply_to(message, f"✅ تعرفه هر گیگابایت مازاد روی {val:,} تومان تنظیم شد.")
    except Exception:
        bot.reply_to(message, "فرمت دستور: <code>/setcost 450</code>")


@bot.message_handler(commands=['interval'])
@admin_only
def handle_setinterval(message):
    try:
        parts = message.text.split()
        val = int(parts[1])
        if val < 5:
            bot.reply_to(message, "حداقل زمان بررسی ۵ دقیقه است.")
            return
        config["check_interval_minutes"] = val
        save_config(config)
        bot.reply_to(message, f"✅ فاصله بررسی روی هر {val} دقیقه تنظیم شد.")
    except Exception:
        bot.reply_to(message, "فرمت دستور: <code>/interval 30</code>")


@bot.message_handler(commands=['toggle_alert'])
@admin_only
def handle_toggle_alert(message):
    config["auto_alert_enabled"] = not config.get("auto_alert_enabled", True)
    save_config(config)
    st = "فعال ✅" if config["auto_alert_enabled"] else "غیرفعال ❌"
    bot.reply_to(message, f"وضعیت هشدار سقف: {st}")


@bot.message_handler(commands=['toggle_spike'])
@admin_only
def handle_toggle_spike(message):
    config["spike_alert_enabled"] = not config.get("spike_alert_enabled", True)
    save_config(config)
    st = "فعال ✅" if config["spike_alert_enabled"] else "غیرفعال ❌"
    bot.reply_to(message, f"وضعیت هشدار جهش ترافیک: {st}")


@bot.message_handler(func=lambda message: True)
@admin_only
def handle_unknown_message(message):
    bot.reply_to(message, "دستور نامشخص است. لطفاً از دکمه‌های پایین صفحه استفاده کنید.", reply_markup=get_main_keyboard())


# ==================== ترد مانیتورینگ هوشمند پس‌زمینه ====================

def background_monitor_worker():
    print("🚀 Background Monitor Worker started.")
    last_alert_status = {}

    while True:
        try:
            cfg = load_config()
            interval_min = cfg.get("check_interval_minutes", 30)
            admin_ids = get_admin_ids()
            limit_gb = cfg.get("limit_gb", 8.0)
            cost_gb = cfg.get("cost_per_gb_toman", 450)
            auto_alert = cfg.get("auto_alert_enabled", True)
            spike_alert = cfg.get("spike_alert_enabled", True)
            send_summary = cfg.get("send_periodic_summary", True)

            if admin_ids:
                limit_items = arvan_client.check_8gb_limits(limit_gb=limit_gb)

                for item in limit_items:
                    domain = item["domain"]
                    pct = item.get("percent_used", 0.0)
                    is_exceeded = item.get("is_exceeded", False)
                    is_warning = item.get("is_warning", False)

                    prev_state = last_alert_status.get(domain, "safe")
                    curr_state = "exceeded" if is_exceeded else ("warning" if is_warning else "safe")

                    # ۱. هشدار عبور از سقف یا نزدیک شدن به آن
                    if auto_alert and (is_exceeded or is_warning):
                        eta_info = arvan_client.calculate_burn_rate_and_eta(domain, limit_gb=limit_gb)
                        cost_info = arvan_client.calculate_overage_cost(item["bypass_bytes"], limit_gb=limit_gb, cost_per_gb_toman=cost_gb)

                        alert_msg = (
                            f"{item['status_emoji']} <b>هشدار مصرف سقف CDN ابر آروان!</b>\n\n"
                            f"🌐 دامنه: <code>{domain}</code>\n"
                            f"📦 ترافیک Bypass: <b>{format_bytes(item['bypass_bytes'])}</b> ({pct:.1f}%)\n"
                            f"⏳ <b>تخمین اتمام:</b> {eta_info['eta_time_str']}\n"
                        )
                        if is_exceeded:
                            alert_msg += f"🚨 <b>مصرف مازاد:</b> {cost_info['cost_readable']}\n"

                        for cid in admin_ids:
                            try:
                                bot.send_message(cid, alert_msg)
                            except Exception as e:
                                print(f"Error sending alert to {cid}: {e}")

                    # ۲. تشخیص جهش ناگهانی (Spike Detection)
                    if spike_alert:
                        spike = arvan_client.detect_traffic_spike(domain)
                        if spike:
                            spike_msg = (
                                f"⚡️ <b>هشدار جهش ناگهانی ترافیک (Spike)!</b>\n\n"
                                f"🌐 دامنه: <code>{domain}</code>\n"
                                f"📈 ترافیک ساعت اخیر: <b>{spike['latest_hour_readable']}</b> ({spike['multiplier']:.1f} برابر میانگین قبلی!)\n"
                                f"💡 بررسی کنید که آیا بات‌ها یا درخواست‌های نامتعارف در جریان است یا خیر."
                            )
                            for cid in admin_ids:
                                try:
                                    bot.send_message(cid, spike_msg)
                                except Exception as e:
                                    print(f"Error sending spike alert to {cid}: {e}")

                    last_alert_status[domain] = curr_state

                # ۳. ارسال خلاصه دوره‌ای وضعیت
                if send_summary:
                    summary_text = (
                        f"⏰ <b>گزارش دوره‌ای مانیتورینگ CDN</b> (هر {interval_min} دقیقه)\n\n" +
                        format_status_message(arvan_client, limit_gb, cost_gb)
                    )
                    for cid in admin_ids:
                        try:
                            bot.send_message(cid, summary_text)
                        except Exception as e:
                            print(f"Error sending summary to {cid}: {e}")

        except Exception as e:
            print(f"Error in background monitor: {e}")

        sleep_secs = max(60, config.get("check_interval_minutes", 30) * 60)
        time.sleep(sleep_secs)


def main():
    cfg = load_config()
    token = cfg.get("telegram_bot_token", "").strip()
    if not token or token == "YOUR_TELEGRAM_BOT_TOKEN" or ":" not in token:
        print("\n" + "=" * 75)
        print("⚠️ خطا: توکن ربات تلگرام تنظیم نشده است!")
        print(f"لطفاً فایل {CONFIG_FILE} را باز کرده و توکن ربات خود را در 'telegram_bot_token' قرار دهید.")
        print("یا متغیر محیطی TELEGRAM_BOT_TOKEN را ست کنید:")
        print("   export TELEGRAM_BOT_TOKEN=\"123456789:ABCdefGhIjk...\"")
        print("راهنما: توکن ربات را می‌توانید با ارسال دستور /newbot به آیدی @BotFather دریافت کنید.")
        print("=" * 75 + "\n")
        sys.exit(1)

    admins = get_admin_ids()
    print("=" * 75)
    print("🤖 در حال راه‌اندازی ربات تلگرام مانیتورینگ پیشرفته CDN ابر آروان...")
    if admins:
        print(f"🔒 حالت امنیتی فعال: ربات منحصراً به {len(admins)} ادمین پاسخ می‌دهد: {admins}")
    else:
        print("⚠️ توجه: هنوز هیچ Chat ID ادمینی در bot_config.json ثبت نشده است.")
        print("   با ارسال اولین پیام به ربات، شناسه عددی شما اعلام می‌شود.")
    print("=" * 75)

    t = threading.Thread(target=background_monitor_worker, daemon=True)
    t.start()

    print("✅ ربات تلگرام آماده و در حال دریافت پیام‌ها است.")
    print("📡 برای توقف از Ctrl+C استفاده کنید.")

    while True:
        try:
            bot.infinity_polling(timeout=20, long_polling_timeout=20)
        except Exception as e:
            print(f"Bot polling error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
