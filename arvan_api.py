"""
ArvanCloud CDN API Client & Advanced Analytics Engine
ماژول هسته برای اتصال به API ابر آروان، محاسبات پیش‌بینانه، نمودارهای تحلیلی و مدیریت DNS
"""

import os
import json
import datetime
from datetime import timezone, timedelta
import urllib.request
import urllib.error
import urllib.parse
from io import BytesIO
from typing import List, Dict, Any, Optional, Tuple

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

def _get_default_api_key() -> str:
    env_key = os.getenv("ARVAN_API_KEY")
    if env_key:
        return env_key
    if os.path.exists("bot_config.json"):
        try:
            with open("bot_config.json", "r", encoding="utf-8") as f:
                return json.load(f).get("arvan_api_key", "")
        except Exception:
            pass
    return ""

DEFAULT_API_KEY = _get_default_api_key()
BASE_URL = "https://napi.arvancloud.ir/cdn/4.0"

SUPPORTED_PERIODS = {
    "3h": "۳ ساعت گذشته (3 Hours)",
    "6h": "۶ ساعت گذشته (6 Hours)",
    "12h": "۱۲ ساعت گذشته (12 Hours)",
    "24h": "۲۴ ساعت گذشته (24 Hours)",
    "7d": "۱ هفته گذشته (7 Days)",
    "30d": "۱ ماه گذشته (30 Days)",
}

PERIOD_LIST = ["3h", "6h", "12h", "24h", "7d", "30d"]

TEHRAN_TZ = timezone(timedelta(hours=3, minutes=30))


def format_bytes(size_bytes: Optional[int]) -> str:
    """تبدیل بایت به فرمت‌های خوانا مانند MB, GB, TB"""
    if size_bytes is None:
        return "N/A"
    try:
        val = float(size_bytes)
    except (ValueError, TypeError):
        return "N/A"

    if val < 0:
        val = 0.0

    for unit in ["B", "KB", "MB", "GB", "TB", "PB"]:
        if abs(val) < 1024.0:
            return f"{val:,.2f} {unit}"
        val /= 1024.0
    return f"{val:,.2f} EB"


def format_number(val: Optional[int]) -> str:
    """فرمت‌بندی تعداد با جداکننده کاما"""
    if val is None:
        return "0"
    try:
        return f"{int(val):,}"
    except (ValueError, TypeError):
        return str(val)


def generate_progress_bar(percentage: float, length: int = 12) -> str:
    """رسم نوار پیشرفت متنی"""
    capped_pct = max(0.0, min(100.0, percentage))
    filled_len = int(round(length * capped_pct / 100.0))
    bar = "█" * filled_len + "░" * (length - filled_len)
    return bar


class ArvanCDNClient:
    def __init__(self, api_key: str = DEFAULT_API_KEY, base_url: str = BASE_URL):
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")

    def _request(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        method: str = "GET",
        timeout: int = 25
    ) -> Dict[str, Any]:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        if params:
            query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
            if query:
                url = f"{url}?{query}"

        headers = {
            "Authorization": f"Apikey {self.api_key}",
            "Accept": "application/json",
            "User-Agent": "ArvanTrafficMonitor/3.0"
        }

        body_bytes = None
        if data is not None:
            headers["Content-Type"] = "application/json"
            body_bytes = json.dumps(data).encode("utf-8")

        req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                body = response.read().decode("utf-8")
                return json.loads(body)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8")
            try:
                err_json = json.loads(err_body)
                msg = err_json.get("message") or err_json.get("error") or str(err_json)
            except Exception:
                msg = err_body
            raise RuntimeError(f"HTTP {e.code}: {msg}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"خطای ارتباط با سرور آروان: {e.reason}")

    def get_domains(self) -> List[str]:
        """دریافت نام تمام دامنه‌ها با صفحه‌بندی کامل"""
        domains: List[str] = []
        page = 1
        per_page = 50

        while True:
            res = self._request("domains", params={"page": page, "per_page": per_page})
            data = res.get("data", [])
            if not data:
                break

            for item in data:
                name = item.get("name") or item.get("domain")
                if name:
                    domains.append(name)

            meta = res.get("meta", {})
            last_page = meta.get("last_page", 1)
            if page >= last_page:
                break
            page += 1

        return sorted(list(set(domains)))

    def get_traffic_report(self, domain: str, period: str = "24h") -> Dict[str, Any]:
        """دریافت گزارش تفکیکی ترافیک و درخواست‌ها"""
        try:
            res = self._request(f"domains/{domain}/reports/traffics", params={"period": period})
        except Exception as e:
            return {
                "domain": domain,
                "period": period,
                "error": str(e),
                "total_bytes": 0,
                "saved_bytes": 0,
                "bypass_bytes": 0,
                "bypass_percent": 0.0,
                "total_requests": 0,
                "saved_requests": 0,
                "bypass_requests": 0,
                "charts": {}
            }

        data = res.get("data", {})
        statistics = data.get("statistics", {})
        traffics = statistics.get("traffics", {})
        requests = statistics.get("requests", {})
        charts = data.get("charts", {})

        total_bytes = traffics.get("total") or 0
        saved_bytes = traffics.get("saved") or 0
        bypass_bytes = traffics.get("bypass")
        if bypass_bytes is None:
            bypass_bytes = max(0, total_bytes - saved_bytes)

        bypass_percent = (bypass_bytes / total_bytes * 100.0) if total_bytes > 0 else 0.0

        total_reqs = requests.get("total") or 0
        saved_reqs = requests.get("saved") or 0
        bypass_reqs = requests.get("bypass")
        if bypass_reqs is None:
            bypass_reqs = max(0, total_reqs - saved_reqs)

        req_bypass_percent = (bypass_reqs / total_reqs * 100.0) if total_reqs > 0 else 0.0

        return {
            "domain": domain,
            "period": period,
            "error": None,
            "total_bytes": total_bytes,
            "saved_bytes": saved_bytes,
            "bypass_bytes": bypass_bytes,
            "bypass_percent": bypass_percent,
            "total_requests": total_reqs,
            "saved_requests": saved_reqs,
            "bypass_requests": bypass_reqs,
            "req_bypass_percent": req_bypass_percent,
            "top_traffic_time": traffics.get("top"),
            "top_requests_time": requests.get("top"),
            "charts": charts
        }

    def get_full_domain_stats(self, domain: str) -> Dict[str, Dict[str, Any]]:
        results = {}
        for p in PERIOD_LIST:
            results[p] = self.get_traffic_report(domain, period=p)
        return results

    def check_8gb_limits(self, limit_gb: float = 8.0, domains: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """بررسی وضعیت مصرف در برابر سقف رایگان ۲۴ ساعته"""
        if domains is None:
            domains = self.get_domains()

        limit_bytes = limit_gb * (1024 ** 3)
        status_list = []

        for d in domains:
            rep = self.get_traffic_report(d, period="24h")
            if rep.get("error"):
                status_list.append({
                    "domain": d,
                    "error": rep["error"],
                    "bypass_bytes": 0,
                    "bypass_gb": 0.0,
                    "limit_gb": limit_gb,
                    "percent_used": 0.0,
                    "is_warning": False,
                    "is_exceeded": False,
                    "status_text": "خطا در دریافت",
                    "status_emoji": "❌"
                })
                continue

            bypass_b = rep["bypass_bytes"]
            bypass_gb = bypass_b / (1024 ** 3)
            pct = (bypass_b / limit_bytes * 100.0) if limit_bytes > 0 else 0.0

            is_exceeded = pct >= 100.0
            is_warning = pct >= 75.0 and not is_exceeded

            if is_exceeded:
                status_emoji = "🔴"
                status_text = "عبور از سقف مجاز"
            elif is_warning:
                status_emoji = "🟡"
                status_text = "هشدار نزدیک به سقف"
            else:
                status_emoji = "🟢"
                status_text = "عادی (ایمن)"

            status_list.append({
                "domain": d,
                "error": None,
                "total_bytes": rep["total_bytes"],
                "saved_bytes": rep["saved_bytes"],
                "bypass_bytes": bypass_b,
                "bypass_gb": bypass_gb,
                "limit_gb": limit_gb,
                "percent_used": pct,
                "is_warning": is_warning,
                "is_exceeded": is_exceeded,
                "status_text": status_text,
                "status_emoji": status_emoji,
                "total_requests": rep["total_requests"],
                "bypass_requests": rep["bypass_requests"]
            })

        return status_list

    # ==================== ۱. محاسبات پیش‌بینانه و نرخ مصرف (Burn Rate & ETA) ====================

    def calculate_burn_rate_and_eta(self, domain: str, limit_gb: float = 8.0) -> Dict[str, Any]:
        """
        محاسبه سرعت مصرف فعلی (بر اساس ۳ ساعت گذشته) و تخمین زمان دقیق اتمام سقف ۸ گیگابایت
        """
        rep_24h = self.get_traffic_report(domain, period="24h")
        rep_3h = self.get_traffic_report(domain, period="3h")

        limit_bytes = limit_gb * (1024 ** 3)
        byp_24h = rep_24h.get("bypass_bytes", 0)
        byp_3h = rep_3h.get("bypass_bytes", 0)

        # محاسبه سرعت مصرف در هر ساعت (بر اساس میانگین ۳ ساعت اخیر)
        hourly_rate_bytes = byp_3h / 3.0
        remaining_bytes = max(0, limit_bytes - byp_24h)

        is_exceeded = byp_24h >= limit_bytes

        now_tehran = datetime.datetime.now(TEHRAN_TZ)

        if is_exceeded:
            eta_hours = 0.0
            eta_time_str = "هم‌اکنون تمام شده است! 🚨"
            urgency = "danger"
        elif hourly_rate_bytes > 5 * 1024 * 1024:  # بیشتر از ۵ مگابایت در ساعت
            eta_hours = remaining_bytes / hourly_rate_bytes
            target_time = now_tehran + timedelta(hours=eta_hours)
            if eta_hours < 24:
                eta_time_str = f"حدود {eta_hours:.1f} ساعت دیگر (ساعت {target_time.strftime('%H:%M')} به وقت تهران)"
            else:
                eta_time_str = f"بیش از ۲۴ ساعت دیگر ({target_time.strftime('%Y-%m-%d %H:%M')})"
            urgency = "warning" if eta_hours <= 6.0 else "safe"
        else:
            eta_hours = None
            eta_time_str = "امن (نرخ مصرف بسیار پایین، سقف به اتمام نخواهد رسید)"
            urgency = "safe"

        return {
            "domain": domain,
            "bypass_24h_bytes": byp_24h,
            "bypass_24h_readable": format_bytes(byp_24h),
            "remaining_bytes": remaining_bytes,
            "remaining_readable": format_bytes(remaining_bytes),
            "hourly_burn_rate_bytes": int(hourly_rate_bytes),
            "hourly_burn_rate_readable": f"{format_bytes(int(hourly_rate_bytes))}/ساعت",
            "is_exceeded": is_exceeded,
            "eta_hours": eta_hours,
            "eta_time_str": eta_time_str,
            "urgency": urgency
        }

    def detect_traffic_spike(self, domain: str) -> Optional[Dict[str, Any]]:
        """
        تشخیص جهش ناگهانی ترافیک (Spike/DDoS) با مقایسه ساعت گذشته نسبت به میانگین ۲۴ ساعته
        """
        rep = self.get_traffic_report(domain, period="24h")
        charts = rep.get("charts", {})
        traffics_chart = charts.get("traffics", {})
        series = traffics_chart.get("series", [])

        bypass_series = None
        for s in series:
            if "bypass" in s.get("name", "").lower():
                bypass_series = s.get("data", [])
                break

        if not bypass_series or len(bypass_series) < 4:
            return None

        # مقایسه داده‌های آخرین ساعت با میانگین ساعات قبلی
        latest_hour_bytes = bypass_series[-1]
        previous_hours = bypass_series[:-1]
        avg_hourly_bytes = sum(previous_hours) / len(previous_hours) if previous_hours else 1

        # اگر مصرف ساعت اخیر حداقل ۳ برابر میانگین قبلی باشد و بیش از ۱۵۰ مگابایت باشد
        if latest_hour_bytes >= (2.8 * avg_hourly_bytes) and latest_hour_bytes > (150 * 1024 * 1024):
            ratio = latest_hour_bytes / avg_hourly_bytes if avg_hourly_bytes > 0 else 0
            return {
                "domain": domain,
                "latest_hour_bytes": latest_hour_bytes,
                "latest_hour_readable": format_bytes(latest_hour_bytes),
                "avg_hourly_readable": format_bytes(int(avg_hourly_bytes)),
                "multiplier": ratio
            }
        return None

    def calculate_overage_cost(self, bypass_bytes: int, limit_gb: float = 8.0, cost_per_gb_toman: int = 450) -> Dict[str, Any]:
        """محاسبه هزینه مالی ترافیک مازاد به تومان"""
        limit_bytes = limit_gb * (1024 ** 3)
        overage_bytes = max(0, bypass_bytes - limit_bytes)
        overage_gb = overage_bytes / (1024 ** 3)
        cost_toman = int(round(overage_gb * cost_per_gb_toman))
        return {
            "overage_bytes": overage_bytes,
            "overage_gb": overage_gb,
            "cost_toman": cost_toman,
            "cost_readable": f"{cost_toman:,} تومان"
        }

    # ==================== ۲. مدیریت رکوردهای DNS و سوییچ ابر روشن/خاموش ====================

    def get_dns_records(self, domain: str) -> List[Dict[str, Any]]:
        """دریافت تمام رکوردهای DNS یک دامنه"""
        res = self._request(f"domains/{domain}/dns-records")
        return res.get("data", [])

    def toggle_dns_cloud(self, domain: str, record_id: str, cloud_status: bool) -> bool:
        """تغییر وضعیت ابر روشن/خاموش (Cloud/Proxy) یک رکورد DNS"""
        payload = {"cloud": cloud_status}
        res = self._request(
            f"domains/{domain}/dns-records/{record_id}/cloud",
            data=payload,
            method="PUT"
        )
        return bool(res.get("data") or res.get("status") is not False)

    # ==================== ۳. ریشه‌یابی و منابع مصرف (IPهای پرمصرف / مناطق) ====================

    def get_top_traffic_sources(self, domain: str, period: str = "24h") -> Dict[str, Any]:
        """
        استخراج منابع اصلی ترافیک (IPهای پرمصرف در صورت دسترسی پلن، یا مناطق جغرافیایی پرمصرف)
        """
        # ۱. تلاش برای دریافت لیست IPها (نیاز به پلن حرفه‌ای/سازمانی در آروان)
        try:
            res_ips = self._request(f"domains/{domain}/reports/high-request-ips", params={"period": period})
            ips = res_ips.get("data", [])
            if ips:
                return {"type": "ips", "data": ips[:10]}
        except Exception:
            pass

        # ۲. در پلن Basic آروان: دریافت لیست مناطق و کشورهای پرمصرف
        try:
            res_map = self._request(f"domains/{domain}/reports/traffics/map", params={"period": period})
            lists = res_map.get("data", {}).get("lists", [])
            return {"type": "geo", "data": lists[:10]}
        except Exception as e:
            return {"type": "error", "error": str(e), "data": []}

    # ==================== ۴. تولید نمودارها و اینفوگرافیک‌ها ====================

    def generate_chart_image(self, domain: str, period: str = "24h", output_file: Optional[str] = None) -> bytes:
        """تولید نمودار خطی تحلیلی ترافیک و درخواست‌ها"""
        rep = self.get_traffic_report(domain, period=period)
        if rep.get("error"):
            raise ValueError(f"نمی‌توان نمودار را رسم کرد: {rep['error']}")

        charts = rep.get("charts", {})
        traffics_chart = charts.get("traffics", {})
        requests_chart = charts.get("requests", {})

        categories = traffics_chart.get("categories", [])
        if not categories:
            raise ValueError("داده‌ای برای محور زمانی موجود نیست.")

        parsed_dates = []
        for cat in categories:
            try:
                dt = datetime.datetime.fromisoformat(cat.replace("Z", "+00:00"))
                parsed_dates.append(dt)
            except Exception:
                parsed_dates.append(cat)

        plt.style.use('dark_background')
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
        fig.patch.set_facecolor('#1e1e2e')
        ax1.set_facecolor('#181825')
        ax2.set_facecolor('#181825')

        traffic_series = traffics_chart.get("series", [])
        colors = {"total": "#89b4fa", "bypass": "#f38ba8", "saved": "#a6e3a1"}

        for s in traffic_series:
            raw_name = s.get("name", "")
            key = raw_name.split(".")[-1].lower()
            vals_mb = [v / (1024 * 1024) for v in s.get("data", [])]
            label_map = {"total": "Total Traffic", "bypass": "Bypass (Origin)", "saved": "Saved (Cached)"}
            label = label_map.get(key, raw_name)
            color = colors.get(key, "#cdd6f4")
            ax1.plot(parsed_dates, vals_mb, marker='o', markersize=3, label=label, color=color, linewidth=2)
            if key == "bypass":
                ax1.fill_between(parsed_dates, vals_mb, alpha=0.25, color=color)

        ax1.set_title(f"ArvanCloud CDN - Traffic Timeline: {domain} [{period}]", fontsize=13, fontweight='bold', color='#cdd6f4', pad=12)
        ax1.set_ylabel("Traffic (MB)", fontsize=11, color='#cdd6f4')
        ax1.grid(True, linestyle='--', alpha=0.3, color='#585b70')
        ax1.legend(loc="upper left", framealpha=0.8, facecolor="#313244", edgecolor="#45475a")

        req_colors = {"total": "#b4befe", "bypass": "#fab387", "saved": "#94e2d5"}
        for s in requests_chart.get("series", []):
            raw_name = s.get("name", "")
            key = raw_name.split(".")[-1].lower()
            vals_reqs = s.get("data", [])
            label_map = {"total": "Total Requests", "bypass": "Bypass Requests", "saved": "Saved Requests"}
            ax2.plot(parsed_dates, vals_reqs, marker='s', markersize=3, label=label_map.get(key, raw_name), color=req_colors.get(key, "#f5e0dc"), linewidth=1.8)

        ax2.set_ylabel("Requests Count", fontsize=11, color='#cdd6f4')
        ax2.set_xlabel("Time (UTC)", fontsize=11, color='#cdd6f4', labelpad=10)
        ax2.grid(True, linestyle='--', alpha=0.3, color='#585b70')
        ax2.legend(loc="upper left", framealpha=0.8, facecolor="#313244", edgecolor="#45475a")

        if parsed_dates and isinstance(parsed_dates[0], datetime.datetime):
            date_fmt = mdates.DateFormatter('%H:%M' if period in ["3h", "6h", "12h", "24h"] else '%b %d')
            ax2.xaxis.set_major_formatter(date_fmt)
            fig.autofmt_xdate(rotation=35)

        plt.tight_layout()
        buf = BytesIO()
        plt.savefig(buf, format='png', dpi=140, bbox_inches='tight')
        plt.close(fig)
        buf.seek(0)
        img_bytes = buf.getvalue()
        if output_file:
            with open(output_file, "wb") as f:
                f.write(img_bytes)
        return img_bytes

    def generate_pie_chart_image(self, domains: Optional[List[str]] = None, output_file: Optional[str] = None) -> bytes:
        """
        تولید نمودار دونات شیک (Donut Chart) از سهم هر دامنه در مصرف ترافیک ۲۴ ساعت گذشته
        """
        if domains is None:
            domains = self.get_domains()

        domain_traffic = {}
        for d in domains:
            rep = self.get_traffic_report(d, period="24h")
            domain_traffic[d] = rep.get("bypass_bytes", 0)

        total_bytes = sum(domain_traffic.values())
        if total_bytes == 0:
            total_bytes = 1

        labels = []
        sizes = []
        palette = ['#f38ba8', '#fab387', '#f9e2af', '#a6e3a1', '#89b4fa', '#cba6f7']

        for d, b in domain_traffic.items():
            labels.append(f"{d}\n({format_bytes(b)})")
            sizes.append(b)

        plt.style.use('dark_background')
        fig, ax = plt.subplots(figsize=(8, 7))
        fig.patch.set_facecolor('#1e1e2e')
        ax.set_facecolor('#1e1e2e')

        wedges, texts, autotexts = ax.pie(
            sizes,
            labels=labels,
            autopct='%1.1f%%',
            pctdistance=0.75,
            colors=palette[:len(sizes)],
            startangle=140,
            textprops=dict(color="#cdd6f4", fontsize=10),
            wedgeprops=dict(width=0.45, edgecolor='#1e1e2e', linewidth=2)
        )

        for autotext in autotexts:
            autotext.set_color('#11111b')
            autotext.set_fontsize(10)
            autotext.set_weight('bold')

        ax.set_title("ArvanCloud CDN - 24h Bypass Traffic Share", fontsize=14, fontweight='bold', color='#cdd6f4', pad=20)

        # متن مرکز دونات
        ax.text(0, 0, f"Total\n{format_bytes(sum(domain_traffic.values()))}",
                ha='center', va='center', fontsize=12, fontweight='bold', color='#cdd6f4')

        plt.tight_layout()
        buf = BytesIO()
        plt.savefig(buf, format='png', dpi=140, bbox_inches='tight')
        plt.close(fig)
        buf.seek(0)
        img_bytes = buf.getvalue()
        if output_file:
            with open(output_file, "wb") as f:
                f.write(img_bytes)
        return img_bytes

    def generate_daily_digest_card(self, domains: Optional[List[str]] = None, limit_gb: float = 8.0, output_file: Optional[str] = None) -> bytes:
        """
        تولید یک پوستر گرافیکی مدرن (Daily Digest Infographic Card) از خلاصه وضعیت تمام دامنه‌ها
        """
        if domains is None:
            domains = self.get_domains()

        limit_items = self.check_8gb_limits(limit_gb=limit_gb, domains=domains)

        plt.style.use('dark_background')
        fig, ax = plt.subplots(figsize=(10, 6.5))
        fig.patch.set_facecolor('#181825')
        ax.set_facecolor('#1e1e2e')

        y_positions = list(range(len(limit_items)))
        domain_names = [it["domain"] for it in limit_items]
        percentages = [min(100.0, it["percent_used"]) for it in limit_items]
        bar_colors = []
        for it in limit_items:
            if it["is_exceeded"]:
                bar_colors.append('#f38ba8')  # قرمز
            elif it["is_warning"]:
                bar_colors.append('#fab387')  # نارنجی
            else:
                bar_colors.append('#a6e3a1')  # سبز

        # رسم نوار پیش‌زمینه (سقف ۱۰۰٪)
        ax.barh(y_positions, [100] * len(limit_items), height=0.45, color='#313244', alpha=0.5)
        # رسم نوار مقدار مصرف
        bars = ax.barh(y_positions, percentages, height=0.45, color=bar_colors)

        ax.set_yticks(y_positions)
        ax.set_yticklabels(domain_names, fontsize=12, fontweight='bold', color='#cdd6f4')
        ax.set_xlim(0, 115)
        ax.set_xlabel("Usage Percentage of 8 GB Limit (%)", fontsize=11, color='#a6adc8', labelpad=10)
        ax.set_title("ArvanCloud CDN - Daily Traffic Digest Card", fontsize=15, fontweight='bold', color='#cdd6f4', pad=18)
        ax.grid(axis='x', linestyle='--', alpha=0.25, color='#585b70')

        # افزودن برچسب‌های متنی روی میله‌ها
        for idx, (bar, item) in enumerate(zip(bars, limit_items)):
            width = bar.get_width()
            val_text = f"{format_bytes(item['bypass_bytes'])} ({item['percent_used']:.1f}%)"
            ax.text(width + 2, bar.get_y() + bar.get_height() / 2, val_text,
                    va='center', ha='left', fontsize=10, fontweight='bold', color='#cdd6f4')

        # خلاصه در زیر نمودار
        tot_bypass = sum(it["bypass_bytes"] for it in limit_items)
        now_str = datetime.datetime.now(TEHRAN_TZ).strftime("%Y-%m-%d %H:%M")
        fig.text(0.12, 0.03, f"Date: {now_str} (Tehran) | Total Account Bypass: {format_bytes(tot_bypass)}",
                 fontsize=10, color='#6c7086')

        plt.tight_layout()
        buf = BytesIO()
        plt.savefig(buf, format='png', dpi=140, bbox_inches='tight')
        plt.close(fig)
        buf.seek(0)
        img_bytes = buf.getvalue()
        if output_file:
            with open(output_file, "wb") as f:
                f.write(img_bytes)
        return img_bytes
