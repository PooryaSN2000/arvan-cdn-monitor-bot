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

try:
    from nova_api import NovaClient
except ImportError:
    NovaClient = None

CONFIG_FILE = "bot_config.json"

DEFAULT_CONFIG = {
    "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
    "admin_chat_ids": [],
    "arvan_api_key": os.getenv("ARVAN_API_KEY", DEFAULT_API_KEY),
    "limit_gb": 8.0,
    "cost_per_gb_toman": 1500,
    "check_interval_minutes": 30,
    "auto_alert_enabled": True,
    "alert_threshold_percent": 75.0,
    "send_periodic_summary": True,
    "spike_alert_enabled": True,
    "nova_api_url": os.getenv("NOVA_API_URL", ""),
    "nova_api_key": os.getenv("NOVA_API_KEY", "")
}


def get_nova_client() -> Optional[Any]:
    """Retrieve NovaClient instance if configured, otherwise None."""
    if NovaClient is None:
        return None
    cfg = load_config()
    url = cfg.get("nova_api_url", "").strip()
    key = cfg.get("nova_api_key", "").strip()
    if url and key and not key.startswith("YOUR_"):
        try:
            return NovaClient(api_url=url, api_key=key)
        except Exception:
            return None
    return None


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


# ==================== Admin Authentication Decorator ====================

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
    if get_nova_client() is not None:
        markup.add(types.KeyboardButton("🛡 پنل VPN (کاربران آنلاین و مصرف)"))
    return markup


# ==================== Status Report Generator ====================

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

    if warnings:
        lines.append("\n⚠️ <b>دامنه‌های در آستانه سقف (بالای ۷۵٪):</b>")
        for it in warnings:
            try:
                eta_info = client.calculate_burn_rate_and_eta(it["domain"], limit_gb=limit_gb)
                eta_str = f" | ⏳ {eta_info['eta_time_str']}" if eta_info.get("eta_time_str") else ""
            except Exception:
                eta_str = ""
            lines.append(f"  🔸 <b>{it['domain']}</b>: {it['bypass_gb']:.2f} GB ({it['percent_used']:.1f}%){eta_str}")

    if not exceeded and not warnings:
        lines.append("\n✅ تمامی دامنه‌ها در محدوده امن قرار دارند.")

    return "\n".join(lines)


# ==================== Message & Command Handlers ====================

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
            config.get("cost_per_gb_toman", 1500)
        )
        bot.send_message(chat_id, report, reply_markup=get_main_keyboard())
    except Exception as e:
        bot.send_message(chat_id, f"❌ خطا در دریافت گزارش: {e}")


# ==================== 1. Burn Rate & ETA Prediction ====================

@bot.message_handler(func=lambda msg: msg.text in ["⏱ پیش‌بینی اتمام سقف (ETA)", "/eta"])
@admin_only
def handle_eta(message):
    chat_id = message.chat.id
    bot.send_chat_action(chat_id, "typing")
    try:
        domains = arvan_client.get_domains()
        limit_gb = config.get("limit_gb", 8.0)
        cost_gb = config.get("cost_per_gb_toman", 1500)

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


# ==================== 2. Infographics & Charts Menu ====================

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


# ==================== 3. ArvanCloud DNS Cloud/Proxy Switch ====================

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
        # Filter proxyable records (A, AAAA, CNAME)
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


# ==================== 4. Root-Cause Analysis & Top Traffic Sources (IP / Geo) ====================

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


# ==================== Other Handlers & Administrative Commands ====================

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
    cost = config.get("cost_per_gb_toman", 1500)
    auto_alert = "فعال ✅" if config.get("auto_alert_enabled", True) else "غیرفعال ❌"
    spike_alert = "فعال ✅" if config.get("spike_alert_enabled", True) else "غیرفعال ❌"
    summary = "فعال ✅" if config.get("send_periodic_summary", True) else "غیرفعال ❌"
    nova_status = "متصل و فعال ✅" if get_nova_client() is not None else "غیرفعال (تنظیم نشده) ⚪️"

    admins_str = ", ".join(f"<code>{a}</code>" for a in admins) if admins else "تنظیم نشده"

    text = (
        "⚙️ <b>تنظیمات پیشرفته سامانه مانیتورینگ:</b>\n\n"
        f"👑 <b>شناسه‌های ادمین:</b> {admins_str}\n"
        f"⏱ <b>فاصله زمانی بررسی:</b> هر {interval} دقیقه یکبار\n"
        f"🎯 <b>سقف محدودیت رایگان:</b> {limit} گیگابایت در ۲۴ ساعت\n"
        f"💰 <b>تعرفه هر گیگ مازاد:</b> {cost} تومان\n"
        f"🔔 <b>هشدار عبور از سقف:</b> {auto_alert}\n"
        f"⚡️ <b>تشخیص جهش ناگهانی (Spike):</b> {spike_alert}\n"
        f"📋 <b>ارسال خلاصه وضعیت دوره‌ای:</b> {summary}\n"
        f"🛡 <b>پنل VPN (Nova):</b> {nova_status}\n\n"
        "<b>دستورات مدیریتی ادمین:</b>\n"
        "• <code>/addadmin 123456789</code> - اضافه کردن ادمین\n"
        "• <code>/deladmin 123456789</code> - حذف ادمین\n"
        "• <code>/admins</code> - مشاهده لیست ادمین‌ها\n"
        "• <code>/setlimit 8.0</code> - تغییر سقف مجاز (گیگابایت)\n"
        "• <code>/setcost 1500</code> - تنظیم تعرفه هر گیگ به تومان\n"
        "• <code>/interval 30</code> - تنظیم فاصله بررسی (دقیقه)\n"
        "• <code>/toggle_alert</code> - روشن/خاموش کردن هشدار سقف\n"
        "• <code>/toggle_spike</code> - روشن/خاموش کردن هشدار جهش ناگهانی\n"
        "• <code>/toggle_summary</code> - روشن/خاموش کردن گزارش دوره‌ای"
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
        bot.reply_to(message, "فرمت دستور: <code>/setcost 1500</code>")


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


@bot.message_handler(commands=['toggle_summary'])
@admin_only
def handle_toggle_summary(message):
    config["send_periodic_summary"] = not config.get("send_periodic_summary", True)
    save_config(config)
    st = "فعال ✅" if config["send_periodic_summary"] else "غیرفعال ❌"
    bot.reply_to(message, f"وضعیت ارسال گزارش دوره‌ای (هر {config.get('check_interval_minutes', 30)} دقیقه): {st}")


# ==================== Nova VPN Panel (Optional Integration) ====================

@bot.message_handler(func=lambda msg: msg.text in ["🛡 پنل VPN (کاربران آنلاین و مصرف)", "/vpn", "/nova"])
@admin_only
def handle_nova_menu(message):
    chat_id = message.chat.id
    client = get_nova_client()
    if not client:
        bot.send_message(chat_id, "⚠️ پنل VPN تنظیم نشده است یا در فایل تنظیمات فعال نیست.")
        return

    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🟢 کاربران آنلاین و مصرف", callback_data="nova:online"),
        types.InlineKeyboardButton("📊 سلامت زنده سرور", callback_data="nova:stats"),
        types.InlineKeyboardButton("⚠️ کاربران نیازمند تمدید", callback_data="nova:alerts"),
        types.InlineKeyboardButton("👥 تمام کاربران و وضعیت حجم", callback_data="nova:users:0"),
        types.InlineKeyboardButton("🔄 تست اتصال به پنل", callback_data="nova:health")
    )

    welcome_nova = (
        "🛡 <b>مدیریت و مانیتورینگ پیشرفته پنل VPN (Nova)</b>\n\n"
        "💡 لطفاً یکی از گزینه‌های زیر را انتخاب فرمایید:\n\n"
        "• <b>🟢 کاربران آنلاین:</b> مشاهده کاربران متصل، دستگاه‌های فعال و حجم مصرفی\n"
        "• <b>📊 سلامت زنده سرور:</b> درصد مصرف CPU, RAM, دیسک، وضعیت هسته‌ها و اتصالات زنده\n"
        "• <b>⚠️ کاربران نیازمند تمدید:</b> افراد در آستانه اتمام سقف (بالای ۸۵٪) یا انقضا (کمتر از ۳ روز)\n"
        "• <b>👥 تمام کاربران:</b> کارت مشخصات هر کاربر، افزایش حجم، ریست مصرف و فعال/غیرفعال‌سازی"
    )
    bot.send_message(chat_id, welcome_nova, reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data == "nova:menu")
@admin_only
def callback_nova_menu(call):
    chat_id = call.message.chat.id
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🟢 کاربران آنلاین و مصرف", callback_data="nova:online"),
        types.InlineKeyboardButton("📊 سلامت زنده سرور", callback_data="nova:stats"),
        types.InlineKeyboardButton("⚠️ کاربران نیازمند تمدید", callback_data="nova:alerts"),
        types.InlineKeyboardButton("👥 تمام کاربران و وضعیت حجم", callback_data="nova:users:0"),
        types.InlineKeyboardButton("🔄 تست اتصال به پنل", callback_data="nova:health")
    )
    welcome_nova = (
        "🛡 <b>مدیریت و مانیتورینگ پیشرفته پنل VPN (Nova)</b>\n\n"
        "💡 لطفاً یکی از گزینه‌های زیر را انتخاب فرمایید:\n\n"
        "• <b>🟢 کاربران آنلاین:</b> مشاهده کاربران متصل، دستگاه‌های فعال و حجم مصرفی\n"
        "• <b>📊 سلامت زنده سرور:</b> درصد مصرف CPU, RAM, دیسک، وضعیت هسته‌ها و اتصالات زنده\n"
        "• <b>⚠️ کاربران نیازمند تمدید:</b> افراد در آستانه اتمام سقف (بالای ۸۵٪) یا انقضا (کمتر از ۳ روز)\n"
        "• <b>👥 تمام کاربران:</b> کارت مشخصات هر کاربر، افزایش حجم، ریست مصرف و فعال/غیرفعال‌سازی"
    )
    try:
        bot.edit_message_text(welcome_nova, chat_id=chat_id, message_id=call.message.message_id, reply_markup=markup)
    except Exception:
        bot.send_message(chat_id, welcome_nova, reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data == "nova:online")
@admin_only
def callback_nova_online(call):
    chat_id = call.message.chat.id
    client = get_nova_client()
    if not client:
        bot.answer_callback_query(call.id, text="پنل VPN تنظیم نشده است.", show_alert=True)
        return

    bot.answer_callback_query(call.id, text="در حال استخراج کاربران آنلاین...")
    try:
        summary = client.get_online_summary()
        users = summary.get("users", [])
        total_online = summary.get("total_online_users", len(users))
        total_devices = summary.get("total_devices", sum(u.get("devices", 0) for u in users))

        if not users:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("🔄 به‌روزرسانی", callback_data="nova:online"))
            markup.add(types.InlineKeyboardButton("🔙 بازگشت به منوی VPN", callback_data="nova:menu"))
            bot.edit_message_text(
                "🟢 <b>کاربران آنلاین پنل VPN:</b>\n\n"
                "هم‌اکنون هیچ کاربری آنلاین نیست (تعداد دستگاه‌های متصل: 0).",
                chat_id=chat_id,
                message_id=call.message.message_id,
                reply_markup=markup
            )
            return

        lines = [
            "🟢 <b>کاربران آنلاین پنل VPN (Nova)</b>",
            f"⚡️ <b>تعداد کاربران متصل:</b> {total_online} نفر | <b>کل دستگاه‌های فعال:</b> {total_devices}\n"
        ]

        markup = types.InlineKeyboardMarkup(row_width=2)
        user_buttons = []

        for u in users:
            uid = u["id"]
            name = u["name"]
            devs = u["devices"]
            used_b = u["used_bytes"]
            quota_b = u["quota_bytes"]
            pct = (used_b / quota_b * 100.0) if quota_b > 0 else 0.0
            bar = generate_progress_bar(pct, length=10)
            status_dot = "🟢" if u.get("enabled", True) else "🔴"

            lines.append(
                f"{status_dot} 👤 <b>{name}</b> ({devs} دستگاه 📱)\n"
                f"  • مصرف حجم: <b>{format_bytes(used_b)}</b> از {format_bytes(quota_b)} ({pct:.1f}%)\n"
                f"  • پیشرفت: <code>[{bar}]</code>\n"
            )
            user_buttons.append(types.InlineKeyboardButton(f"⚙️ {name}", callback_data=f"nova:user:{uid}"))

        if user_buttons:
            markup.add(*user_buttons)

        markup.add(
            types.InlineKeyboardButton("🔄 به‌روزرسانی آنلاین‌ها", callback_data="nova:online"),
            types.InlineKeyboardButton("🔙 بازگشت", callback_data="nova:menu")
        )

        bot.edit_message_text(
            "\n".join(lines),
            chat_id=chat_id,
            message_id=call.message.message_id,
            reply_markup=markup
        )
    except Exception as e:
        bot.send_message(chat_id, f"❌ خطا در دریافت اطلاعات کاربران آنلاین: {e}")


@bot.callback_query_handler(func=lambda call: call.data.startswith("nova:users:"))
@admin_only
def callback_nova_all_users(call):
    chat_id = call.message.chat.id
    client = get_nova_client()
    if not client:
        bot.answer_callback_query(call.id, text="پنل VPN تنظیم نشده است.", show_alert=True)
        return

    page = int(call.data.split(":")[2])
    bot.answer_callback_query(call.id, text="دریافت لیست کاربران...")

    try:
        all_users = client.get_all_users()
        online_data = client._request("online").get("online", {})
        page_size = 8
        total_users = len(all_users)
        total_pages = max(1, (total_users + page_size - 1) // page_size)

        page = max(0, min(page, total_pages - 1))
        page_users = all_users[page * page_size : (page + 1) * page_size]

        lines = [
            f"👥 <b>لیست تمام کاربران پنل VPN</b> (صفحه {page + 1} از {total_pages})",
            f"📊 کل کاربران ثبت‌شده: {total_users} نفر\n"
        ]

        markup = types.InlineKeyboardMarkup(row_width=2)
        user_buttons = []

        for idx, u in enumerate(page_users, 1 + page * page_size):
            uid = u["id"]
            name = u.get("name", uid)
            enabled = u.get("enabled", True)
            status_emoji = "🟢" if enabled else "🔴"
            devs = online_data.get(uid, 0)
            conn_emoji = f"⚡️ آنلاین ({devs})" if devs > 0 else "⚪️ آفلاین"
            used_b = u.get("used", 0)
            quota_b = u.get("quotaBytes", 0)
            pct = (used_b / quota_b * 100.0) if quota_b > 0 else 0.0

            lines.append(
                f"{idx}. {status_emoji} <b>{name}</b> ({conn_emoji})\n"
                f"   • مصرف: <b>{format_bytes(used_b)}</b> / {format_bytes(quota_b)} ({pct:.1f}%)\n"
            )
            user_buttons.append(types.InlineKeyboardButton(f"{status_emoji} {name}", callback_data=f"nova:user:{uid}"))

        if user_buttons:
            markup.add(*user_buttons)

        nav_buttons = []
        if page > 0:
            nav_buttons.append(types.InlineKeyboardButton("⬅️ قبلی", callback_data=f"nova:users:{page - 1}"))
        nav_buttons.append(types.InlineKeyboardButton(f"📄 {page + 1} / {total_pages}", callback_data="nova:noop"))
        if page < total_pages - 1:
            nav_buttons.append(types.InlineKeyboardButton("بعدی ➡️", callback_data=f"nova:users:{page + 1}"))

        markup.row(*nav_buttons)
        markup.add(
            types.InlineKeyboardButton("🟢 مشاهده فقط آنلاین‌ها", callback_data="nova:online"),
            types.InlineKeyboardButton("🔙 بازگشت به منوی VPN", callback_data="nova:menu")
        )

        bot.edit_message_text(
            "\n".join(lines),
            chat_id=chat_id,
            message_id=call.message.message_id,
            reply_markup=markup
        )
    except Exception as e:
        bot.send_message(chat_id, f"❌ خطا در دریافت لیست کاربران: {e}")


@bot.callback_query_handler(func=lambda call: call.data.startswith("nova:user:"))
@admin_only
def callback_nova_user_detail(call):
    chat_id = call.message.chat.id
    client = get_nova_client()
    if not client:
        bot.answer_callback_query(call.id, text="پنل VPN تنظیم نشده است.", show_alert=True)
        return

    uid = call.data.split(":")[2]
    bot.answer_callback_query(call.id, text="دریافت مشخصات کاربر...")

    try:
        u = client.get_user(uid)
        if not u:
            bot.send_message(chat_id, f"❌ کاربری با شناسه {uid} یافت نشد.")
            return

        online_data = client._request("online").get("online", {})
        devs = online_data.get(uid, 0)
        conn_str = f"⚡️ آنلاین ({devs} دستگاه فعال)" if devs > 0 else "⚪️ آفلاین"

        name = u.get("name", uid)
        enabled = u.get("enabled", True)
        used_b = u.get("used", 0)
        up_b = u.get("up", 0)
        down_b = u.get("down", 0)
        quota_b = u.get("quotaBytes", 0)
        pct = (used_b / quota_b * 100.0) if quota_b > 0 else 0.0
        bar = generate_progress_bar(pct, length=12)
        expiry = u.get("expiry") or "نامحدود"

        status_text = "فعال 🟢" if enabled else "غیرفعال (مسدود) 🔴"
        next_action_text = "🔴 غیرفعال‌سازی اکانت" if enabled else "🟢 فعال‌سازی اکانت"
        next_state = "disable" if enabled else "enable"

        text = (
            f"👤 <b>مدیریت کاربر: {name}</b>\n\n"
            f"🆔 <b>شناسه:</b> <code>{uid}</code>\n"
            f"🔘 <b>وضعیت اکانت:</b> <b>{status_text}</b>\n"
            f"📡 <b>وضعیت اتصال:</b> {conn_str}\n\n"
            f"📊 <b>میزان مصرف:</b> <b>{format_bytes(used_b)}</b> از <b>{format_bytes(quota_b)}</b> ({pct:.1f}%)\n"
            f"📈 پیشرفت مصرف: <code>[{bar}]</code>\n"
            f"⬆️ آپلود: {format_bytes(up_b)} | ⬇️ دانلود: {format_bytes(down_b)}\n"
            f"⏳ <b>تاریخ انقضا:</b> {expiry}\n"
        )

        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.row(
            types.InlineKeyboardButton("➕ ۱۰ گیگابایت", callback_data=f"nova:add_quota:{uid}:10"),
            types.InlineKeyboardButton("➕ ۲۰ گیگابایت", callback_data=f"nova:add_quota:{uid}:20")
        )
        markup.row(
            types.InlineKeyboardButton("🔄 صفر کردن مصرف", callback_data=f"nova:reset_traffic:{uid}"),
            types.InlineKeyboardButton("⏳ تمدید ۳۰ روز", callback_data=f"nova:extend:{uid}:30")
        )
        markup.row(
            types.InlineKeyboardButton(next_action_text, callback_data=f"nova:toggle:{uid}:{next_state}"),
            types.InlineKeyboardButton("🔄 به‌روزرسانی", callback_data=f"nova:user:{uid}")
        )
        markup.row(
            types.InlineKeyboardButton("🔙 لیست تمام کاربران", callback_data="nova:users:0"),
            types.InlineKeyboardButton("🔙 منوی اصلی VPN", callback_data="nova:menu")
        )

        bot.edit_message_text(
            text,
            chat_id=chat_id,
            message_id=call.message.message_id,
            reply_markup=markup
        )
    except Exception as e:
        bot.send_message(chat_id, f"❌ خطا در دریافت مشخصات کاربر: {e}")


@bot.callback_query_handler(func=lambda call: call.data.startswith("nova:toggle:"))
@admin_only
def callback_nova_toggle_user(call):
    _, _, uid, action = call.data.split(":")
    chat_id = call.message.chat.id
    client = get_nova_client()
    if not client:
        bot.answer_callback_query(call.id, text="پنل VPN تنظیم نشده است.", show_alert=True)
        return

    target_enabled = (action == "enable")
    bot.answer_callback_query(call.id, text="در حال تغییر وضعیت کاربر در پنل...")
    try:
        ok = client.toggle_user(uid, target_enabled)
        if ok:
            st = "فعال شد 🟢" if target_enabled else "غیرفعال شد 🔴"
            bot.answer_callback_query(call.id, text=f"کاربر با موفقیت {st}", show_alert=True)
            # Re-render user detail card
            call.data = f"nova:user:{uid}"
            callback_nova_user_detail(call)
        else:
            bot.answer_callback_query(call.id, text="❌ تغییر وضعیت انجام نشد.", show_alert=True)
    except Exception as e:
        bot.answer_callback_query(call.id, text=f"خطا: {e}", show_alert=True)


@bot.callback_query_handler(func=lambda call: call.data == "nova:health")
@admin_only
def callback_nova_health(call):
    client = get_nova_client()
    if not client:
        bot.answer_callback_query(call.id, text="پنل VPN تنظیم نشده است.", show_alert=True)
        return
    bot.answer_callback_query(call.id, text="بررسی ارتباط با پنل...")
    is_ok = client.is_healthy()
    status_str = "متصل و فعال ✅" if is_ok else "قطع یا خطا در احراز هویت ❌"
    bot.answer_callback_query(call.id, text=f"وضعیت اتصال به پنل: {status_str}", show_alert=True)


@bot.callback_query_handler(func=lambda call: call.data == "nova:noop")
@admin_only
def callback_nova_noop(call):
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("nova:add_quota:"))
@admin_only
def callback_nova_add_quota(call):
    _, _, uid, gb_str = call.data.split(":")
    gb = float(gb_str)
    client = get_nova_client()
    if not client:
        bot.answer_callback_query(call.id, text="پنل VPN تنظیم نشده است.", show_alert=True)
        return

    bot.answer_callback_query(call.id, text=f"در حال افزایش {gb:g} گیگابایت حجم...")
    try:
        ok = client.add_user_quota(uid, gb)
        if ok:
            bot.answer_callback_query(call.id, text=f"✅ {gb:g} گیگابایت با موفقیت به سقف کاربر افزوده شد.", show_alert=True)
            call.data = f"nova:user:{uid}"
            callback_nova_user_detail(call)
        else:
            bot.answer_callback_query(call.id, text="❌ خطا در افزایش حجم کاربر.", show_alert=True)
    except Exception as e:
        bot.answer_callback_query(call.id, text=f"خطا: {e}", show_alert=True)


@bot.callback_query_handler(func=lambda call: call.data.startswith("nova:extend:"))
@admin_only
def callback_nova_extend(call):
    _, _, uid, days_str = call.data.split(":")
    days = int(days_str)
    client = get_nova_client()
    if not client:
        bot.answer_callback_query(call.id, text="پنل VPN تنظیم نشده است.", show_alert=True)
        return

    bot.answer_callback_query(call.id, text=f"در حال تمدید {days} روزه اشتراک...")
    try:
        ok = client.extend_user_expiry(uid, days)
        if ok:
            bot.answer_callback_query(call.id, text=f"✅ تاریخ انقضای کاربر با موفقیت {days} روز تمدید شد.", show_alert=True)
            call.data = f"nova:user:{uid}"
            callback_nova_user_detail(call)
        else:
            bot.answer_callback_query(call.id, text="❌ خطا در تمدید انقضا.", show_alert=True)
    except Exception as e:
        bot.answer_callback_query(call.id, text=f"خطا: {e}", show_alert=True)


@bot.callback_query_handler(func=lambda call: call.data.startswith("nova:reset_traffic:"))
@admin_only
def callback_nova_reset_traffic(call):
    _, _, uid = call.data.split(":")
    client = get_nova_client()
    if not client:
        bot.answer_callback_query(call.id, text="پنل VPN تنظیم نشده است.", show_alert=True)
        return

    bot.answer_callback_query(call.id, text="در حال صفر کردن مصرف کاربر...")
    try:
        ok = client.reset_user_traffic(uid)
        if ok:
            bot.answer_callback_query(call.id, text="✅ مصرف ترافیک کاربر با موفقیت صفر (ریست) شد.", show_alert=True)
            call.data = f"nova:user:{uid}"
            callback_nova_user_detail(call)
        else:
            bot.answer_callback_query(call.id, text="❌ خطا در صفر کردن مصرف کاربر.", show_alert=True)
    except Exception as e:
        bot.answer_callback_query(call.id, text=f"خطا: {e}", show_alert=True)


@bot.callback_query_handler(func=lambda call: call.data == "nova:stats")
@admin_only
def callback_nova_stats(call):
    chat_id = call.message.chat.id
    client = get_nova_client()
    if not client:
        bot.answer_callback_query(call.id, text="پنل VPN تنظیم نشده است.", show_alert=True)
        return

    bot.answer_callback_query(call.id, text="دریافت آمار زنده سرور...")
    try:
        stats = client.get_node_stats()
        system = stats.get("system", {})
        traffic = stats.get("traffic", {})

        # CPU metrics
        cpu = system.get("cpu", {})
        cpu_pct = float(cpu.get("pct", 0))
        cpu_cores = cpu.get("cores", 1)
        cpu_load = cpu.get("load1", 0)
        cpu_bar = generate_progress_bar(cpu_pct, length=10)

        # RAM metrics
        mem = system.get("mem", {})
        mem_pct = float(mem.get("pct", 0))
        mem_used = mem.get("used", 0)
        mem_total = mem.get("total", 0)
        mem_bar = generate_progress_bar(mem_pct, length=10)

        # Disk metrics
        disk = system.get("disk", {})
        disk_pct = float(disk.get("pct", 0))
        disk_used = disk.get("used", 0)
        disk_total = disk.get("total", 0)
        disk_bar = generate_progress_bar(disk_pct, length=10)

        # Tunnel core status
        xray_ok = system.get("xray", False)
        singbox_ok = system.get("singbox", False)
        xray_st = "🟢 آنلاین" if xray_ok else "🔴 آفلاین"
        singbox_st = "🟢 آنلاین" if singbox_ok else "🔴 آفلاین"
        xray_ver = system.get("xrayVersion", "N/A")

        # Network connections
        tcp = system.get("tcpConns", 0)
        udp = system.get("udpConns", 0)

        # Today traffic
        today_up = traffic.get("todayUp", 0)
        today_down = traffic.get("todayDown", 0)
        today_total = traffic.get("today", 0)

        # Server Geo & Hostname
        host = stats.get("host", system.get("hostname", "Node"))
        geo = system.get("geo", {})
        isp = geo.get("isp", "Hetzner")
        country = geo.get("country", "")

        text = (
            f"📊 <b>داشبورد زنده سلامت سرور و منابع (Nova Node)</b>\n"
            f"🌐 <b>هاست:</b> <code>{host}</code> ({country} - {isp})\n\n"
            f"⚙️ <b>مصرف پردازنده (CPU - {cpu_cores} هسته):</b> <b>{cpu_pct:.1f}%</b> (Load: {cpu_load})\n"
            f"<code>[{cpu_bar}]</code>\n\n"
            f"🧠 <b>حافظه رم (RAM):</b> <b>{mem_pct:.1f}%</b> ({format_bytes(mem_used)} / {format_bytes(mem_total)})\n"
            f"<code>[{mem_bar}]</code>\n\n"
            f"💾 <b>فضای دیسک (Disk):</b> <b>{disk_pct:.1f}%</b> ({format_bytes(disk_used)} / {format_bytes(disk_total)})\n"
            f"<code>[{disk_bar}]</code>\n\n"
            f"─────────────────────\n"
            f"🛠 <b>وضعیت سرویس‌های تونل:</b>\n"
            f"  • هسته Xray: {xray_st} (نسخه {xray_ver})\n"
            f"  • هسته Sing-box: {singbox_st}\n\n"
            f"📡 <b>اتصالات فعال زنده:</b>\n"
            f"  • اتصالات TCP فعال: <b>{tcp:,}</b>\n"
            f"  • اتصالات UDP فعال: <b>{udp:,}</b>\n\n"
            f"📦 <b>ترافیک مصرفی سرور در روز جاری:</b>\n"
            f"  • مجموع امروز: <b>{format_bytes(today_total)}</b>\n"
            f"  • ⬆️ آپلود: {format_bytes(today_up)} | ⬇️ دانلود: {format_bytes(today_down)}"
        )

        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("🔄 به‌روزرسانی آمار", callback_data="nova:stats"),
            types.InlineKeyboardButton("🔙 بازگشت به منوی VPN", callback_data="nova:menu")
        )

        bot.edit_message_text(text, chat_id=chat_id, message_id=call.message.message_id, reply_markup=markup)
    except Exception as e:
        bot.send_message(chat_id, f"❌ خطا در دریافت آمار سرور: {e}")


@bot.callback_query_handler(func=lambda call: call.data == "nova:alerts")
@admin_only
def callback_nova_alerts(call):
    chat_id = call.message.chat.id
    client = get_nova_client()
    if not client:
        bot.answer_callback_query(call.id, text="پنل VPN تنظیم نشده است.", show_alert=True)
        return

    bot.answer_callback_query(call.id, text="بررسی سقف و انقضای کاربران...")
    try:
        alerts = client.get_expiring_or_quota_users(quota_threshold_pct=85.0, days_threshold=3)
        nearing_quota = alerts.get("nearing_quota", [])
        nearing_expiry = alerts.get("nearing_expiry", [])

        markup = types.InlineKeyboardMarkup(row_width=2)
        user_buttons = []

        lines = [
            "⚠️ <b>گزارش کاربران نیازمند تمدید (VPN)</b>\n"
        ]

        if not nearing_quota and not nearing_expiry:
            lines.append("✅ همه کاربران در وضعیت مطلوب هستند و در حال حاضر هیچ اکانتی در آستانه اتمام سقف (بالای ۸۵٪) یا انقضا (کمتر از ۳ روز) نیست.")
        else:
            if nearing_quota:
                lines.append("📦 <b>کاربران در آستانه اتمام حجم (بالای ۸۵٪):</b>")
                for u in nearing_quota:
                    uid = u["id"]
                    name = u["name"]
                    used_b = u.get("used", 0)
                    quota_b = u.get("quotaBytes", 0)
                    pct = u.get("pct", 0.0)
                    bar = generate_progress_bar(pct, length=8)
                    lines.append(
                        f"  🔸 👤 <b>{name}</b>: {format_bytes(used_b)} از {format_bytes(quota_b)} ({pct:.1f}%)\n"
                        f"     <code>[{bar}]</code>"
                    )
                    user_buttons.append(types.InlineKeyboardButton(f"⚙️ {name}", callback_data=f"nova:user:{uid}"))
                lines.append("")

            if nearing_expiry:
                lines.append("⏳ <b>کاربران در آستانه انقضای زمانی (کمتر از ۳ روز):</b>")
                for u in nearing_expiry:
                    uid = u["id"]
                    name = u["name"]
                    days = u.get("days_left", 0)
                    exp = u.get("expiry", "")
                    if days < 0:
                        exp_str = f"منقضی شده ({abs(days)} روز پیش)"
                    elif days == 0:
                        exp_str = "امروز منقضی می‌شود!"
                    else:
                        exp_str = f"{days} روز دیگر ({exp})"
                    lines.append(f"  🔸 👤 <b>{name}</b>: {exp_str}")
                    if uid not in [b.callback_data.split(":")[-1] for b in user_buttons]:
                        user_buttons.append(types.InlineKeyboardButton(f"⚙️ {name}", callback_data=f"nova:user:{uid}"))

        if user_buttons:
            markup.add(*user_buttons)

        markup.add(
            types.InlineKeyboardButton("🔄 به‌روزرسانی", callback_data="nova:alerts"),
            types.InlineKeyboardButton("🔙 بازگشت به منوی VPN", callback_data="nova:menu")
        )

        bot.edit_message_text("\n".join(lines), chat_id=chat_id, message_id=call.message.message_id, reply_markup=markup)
    except Exception as e:
        bot.send_message(chat_id, f"❌ خطا در استخراج گزارش تمدید: {e}")


@bot.message_handler(func=lambda message: True)
@admin_only
def handle_unknown_message(message):
    bot.reply_to(message, "دستور نامشخص است. لطفاً از دکمه‌های پایین صفحه استفاده کنید.", reply_markup=get_main_keyboard())


# ==================== Background Intelligent Monitoring Thread ====================

def background_monitor_worker():
    print("🚀 Background Monitor Worker started.")
    last_alert_status = {}
    last_nova_alerted_quota = set()

    while True:
        try:
            cfg = load_config()
            interval_min = cfg.get("check_interval_minutes", 30)
            admin_ids = get_admin_ids()
            limit_gb = cfg.get("limit_gb", 8.0)
            cost_gb = cfg.get("cost_per_gb_toman", 1500)
            auto_alert = cfg.get("auto_alert_enabled", True)
            spike_alert = cfg.get("spike_alert_enabled", True)
            send_summary = cfg.get("send_periodic_summary", True)

            if admin_ids:
                limit_items = arvan_client.check_8gb_limits(limit_gb=limit_gb)

                # 1. Anomaly Traffic Spike Detection
                if spike_alert:
                    for item in limit_items:
                        domain = item["domain"]
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

                # 2. Consolidated Periodic Summary (Includes progress bars, status, warnings & ETA in one single message)
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

                    # Automatically send 24h traffic charts for domains nearing or exceeding quota (>= 75%)
                    warning_items = [it for it in limit_items if it.get("is_warning") or it.get("is_exceeded")]
                    for w_item in warning_items:
                        w_dom = w_item["domain"]
                        try:
                            chart_bytes = arvan_client.generate_chart_image(w_dom, period="24h")
                            pct = w_item.get("percent_used", 0.0)
                            status_title = "عبور از سقف مجاز 🚨" if w_item.get("is_exceeded") else "در آستانه سقف (هشدار) ⚠️"
                            caption = (
                                f"📈 <b>نمودار مصرف ۲۴ ساعته:</b> <code>{w_dom}</code>\n"
                                f"📊 وضعیت: <b>{status_title}</b>\n"
                                f"📦 ترافیک Bypass مصرفی: <b>{format_bytes(w_item['bypass_bytes'])}</b> ({pct:.1f}%)\n"
                                f"💡 بررسی روند ترافیک، کش و ریکوئست‌های مبدا در ۲۴ ساعت گذشته"
                            )
                            for cid in admin_ids:
                                try:
                                    bot.send_photo(cid, chart_bytes, caption=caption)
                                except Exception as e:
                                    print(f"Error sending chart for {w_dom} to {cid}: {e}")
                        except Exception as e:
                            print(f"Error generating 24h chart for warning domain {w_dom}: {e}")

                # 3. If periodic summary is disabled but auto_alert is enabled:
                # Send standalone alert only when a domain changes state (e.g. enters warning or exceeded)
                elif auto_alert:
                    for item in limit_items:
                        domain = item["domain"]
                        pct = item.get("percent_used", 0.0)
                        is_exceeded = item.get("is_exceeded", False)
                        is_warning = item.get("is_warning", False)

                        prev_state = last_alert_status.get(domain, "safe")
                        curr_state = "exceeded" if is_exceeded else ("warning" if is_warning else "safe")

                        if (is_exceeded or is_warning) and curr_state != prev_state:
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

                # 4. Optional VPN Panel Quota Alerts (if Nova panel is configured)
                nova_cli = get_nova_client()
                if nova_cli and auto_alert:
                    try:
                        vpn_alerts = nova_cli.get_expiring_or_quota_users(quota_threshold_pct=90.0, days_threshold=2)
                        for u in vpn_alerts.get("nearing_quota", []):
                            uid = u["id"]
                            if uid not in last_nova_alerted_quota:
                                last_nova_alerted_quota.add(uid)
                                u_name = u.get("name", uid)
                                u_pct = u.get("pct", 0.0)
                                alert_vpn = (
                                    f"⚠️ <b>هشدار پنل VPN: مصرف سقف مجاز کاربر!</b>\n\n"
                                    f"👤 کاربر: <code>{u_name}</code>\n"
                                    f"📦 مصرف: <b>{format_bytes(u.get('used', 0))}</b> از {format_bytes(u.get('quotaBytes', 0))} ({u_pct:.1f}%)\n"
                                    f"💡 جهت افزایش سقف یا تمدید از منوی ربات اقدام فرمایید."
                                )
                                for cid in admin_ids:
                                    try:
                                        bot.send_message(cid, alert_vpn)
                                    except Exception as e:
                                        print(f"Error sending VPN quota alert: {e}")
                    except Exception as e:
                        print(f"Error in background VPN alert check: {e}")

                # Record current domain status to avoid duplicate notifications in subsequent cycles
                for item in limit_items:
                    domain = item["domain"]
                    is_exceeded = item.get("is_exceeded", False)
                    is_warning = item.get("is_warning", False)
                    last_alert_status[domain] = "exceeded" if is_exceeded else ("warning" if is_warning else "safe")

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
