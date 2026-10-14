<div align="center">

# سامانه جامع مانیتورینگ CDN ابر آروان + ربات تلگرام هوشمند (نسخه ۳.۰)

[🇬🇧 English Version](README.md) • [🇮🇷 نسخه فارسی](README.fa.md)

</div>

---

سامانه تخصصی مانیتورینگ CDN ابر آروان با قابلیت پیش‌بینی هوشمند، تشخیص حملات، سوییچ ابر DNS، اینفوگرافیک‌های پیشرفته و اجرای دائمی ۲۴/۷.

---

## ✨ امکانات و قابلیت‌ها

### ۱. 🧠 هوشمندی و پیش‌بینی مصرف (Predictive Monitoring)
- **تخمین دقیق زمان اتمام سقف ۸ گیگ (Burn Rate & ETA):** تحلیل سرعت مصرف فعلی (بر اساس ۳ ساعت گذشته) و محاسبه زمان باقیمانده تا اتمام سقف مجاز رایگان.
- **تشخیص جهش ناگهانی و حملات (Spike / DDoS Detection):** آلارم خودکار در صورتی که ترافیک ساعت اخیر به بیش از ۲.۸ برابر میانگین قبلی برسد.
- **محاسبه هزینه مالی ترافیک مازاد:** تخمین ریالی هزینه مصرف مازاد بر اساس تعرفه هر گیگابایت به تومان.

### ۲. ☁️ سوییچ ابر روشن/خاموش (DNS Cloud Toggle)
- امکان مشاهده رکوردهای DNS هر دامنه و روشن/خاموش کردن حالت پروکسی CDN آروان با یک کلیک از داخل تلگرام.

### ۳. 🎨 اینفوگرافیک‌ها و نمودارهای پیشرفته
- **نمودار تایم‌لاین دوگانه:** خطوط ترافیک کل، کش و بای‌پس + تعداد درخواست‌ها در ۶ بازه زمانی (`3h`, `6h`, `12h`, `24h`, `7d`, `30d`).
- **نمودار دونات سهم دامنه‌ها (Donut Chart):** نمایش درصد سهم هر دامنه از ترافیک ۲۴ ساعت گذشته.
- **پوستر خلاصه روزانه (Daily Digest Card):** کارت گرافیکی شیک شامل میله‌های پیشرفت مصرف تمام دامنه‌ها.

### ۴. 🔍 ریشه‌یابی و منابع مصرف (IPs & Geo Traffic)
- استخراج ۱۰ آدرس IP پرمصرف (در صورت داشتن پلن حرفه‌ای) یا ۱۰ منطقه و کشور پرمصرف از نظر حجم ترافیک و تعداد درخواست.

### ۵. 🔒 امنیت سخت‌گیرانه ادمین
- مسدودسازی دسترسی تمام افراد ناشناس و پاسخ‌دهی منحصراً به ادمین‌های مجاز ثبت‌شده در `bot_config.json`.
- امکان اضافه یا حذف ادمین‌ها با دستورات `/addadmin` و `/deladmin`.

---

## 💻 ۱. استفاده از خط فرمان (CLI)

```bash
# ۱. بررسی سقف ۸ گیگ و وضعیت ۲۴ ساعته دامنه‌ها
python3 arvan_traffic.py

# ۲. تحلیل سرعت مصرف (Burn Rate) و پیش‌بینی ساعت اتمام سقف (ETA)
python3 arvan_traffic.py --eta

# ۳. وضعیت رکوردهای DNS و ابر روشن/خاموش
python3 arvan_traffic.py --dns

# ۴. تولید نمودار دونات سهم دامنه‌ها
python3 arvan_traffic.py --pie

# ۵. تولید پوستر اینفوگرافیک روزانه
python3 arvan_traffic.py --digest

# ۶. آمار کامل ترافیک و درخواست‌ها در تمام ۶ بازه زمانی
python3 arvan_traffic.py --detailed
```

---

## 🤖 ۲. راه‌اندازی ربات تلگرام

### تنظیم فایل `bot_config.json`:
فایل نمونه `bot_config.example.json` را به `bot_config.json` کپی کرده و اطلاعات خود را قرار دهید:
```json
{
  "telegram_bot_token": "YOUR_TELEGRAM_BOT_TOKEN",
  "admin_chat_ids": [123456789],
  "arvan_api_key": "YOUR_ARVAN_API_KEY",
  "limit_gb": 8.0,
  "cost_per_gb_toman": 1500,
  "check_interval_minutes": 30,
  "auto_alert_enabled": true,
  "alert_threshold_percent": 75.0,
  "send_periodic_summary": true,
  "spike_alert_enabled": true
}
```

### اجرای عادی در ترمینال:
```bash
python3 telegram_bot.py
```

---

## ⚙️ ۳. استقرار دائمی ۲۴/۷ روی سرور (Daemon)

برای اینکه ربات همواره در حال اجرا باشد و با ریبوت سرور خودکار روشن شود:

### روش اول: استفاده از Systemd (توصیه شده روی لینوکس)
فایل سرویس آماده در پروژه قرار دارد: [arvan-bot.service](arvan-bot.service)

```bash
# ۱. کپی فایل سرویس در مسیر سیستم
sudo cp arvan-bot.service /etc/systemd/system/

# ۲. فعال‌سازی و استارت سرویس
sudo systemctl daemon-reload
sudo systemctl enable --now arvan-bot

# ۳. بررسی وضعیت سرویس
sudo systemctl status arvan-bot
```

### روش دوم: استفاده از Docker Compose
```bash
docker compose up -d
```
برای مشاهده لاگ‌ها:
```bash
docker compose logs -f
```
