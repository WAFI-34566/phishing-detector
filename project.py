# مشروع التخرج: موقع لفحص الروابط (موثوق أو مشبوه)
# النسخة المعدّلة بعد المراجعة
# التثبيت: pip install flask requests
# التشغيل: python link_checker.py ثم افتح http://localhost:5000

import re
import socket
import ipaddress
import requests
from urllib.parse import urlparse, urljoin
from flask import Flask, request, render_template_string

app = Flask(__name__)

# قوائم الكلمات المشبوهة
bad_words = ["login", "signin", "verify", "update", "secure", "account", "password",
             "bank", "free", "gift", "confirm", "wallet", "billing", "suspended"]
bad_endings = [".xyz", ".top", ".tk", ".ml", ".ga", ".cf", ".click", ".icu", ".buzz", ".work"]
short_links = ["bit.ly", "tinyurl.com", "t.co", "cutt.ly", "is.gd", "rb.gy", "ow.ly", "goo.gl"]

# الشركات المعروفة ومواقعها الحقيقية (كل ما زدت مواقع حقيقية هنا قلّت الأخطاء)
brands = {
    "paypal": ["paypal.com"],
    "google": ["google.com", "google.com.sa", "blog.google"],
    "apple": ["apple.com", "icloud.com"],
    "amazon": ["amazon.com", "amazon.sa"],
    "microsoft": ["microsoft.com"],
    "facebook": ["facebook.com", "fb.com"],
    "netflix": ["netflix.com"],
    "whatsapp": ["whatsapp.com", "whatsapp.net"],
    "stc": ["stc.com.sa"],
    "absher": ["absher.sa", "moi.gov.sa"],
    "alrajhibank": ["alrajhibank.com.sa"],
}

HEADERS = {"User-Agent": "Mozilla/5.0"}
MAX_REDIRECTS = 3


# ---------- دوال مساعدة ----------

def strip_www(host):
    return re.sub(r"^www\.", "", host or "")


def same_site(a, b):
    # نفس الموقع او موقع فرعي منه (www.example.com و example.com)
    a, b = strip_www(a), strip_www(b)
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def is_real(site, domains):
    # هل الموقع هو الدومين الحقيقي او موقع فرعي منه
    for domain in domains:
        if site == domain or site.endswith("." + domain):
            return True
    return False


def clean_variants(text):
    # نرجع الحروف المقلدة لأصلها: paypa1 تصير paypal و g00gle تصير google
    # الرقم 1 ممكن يقلد l او i (m1crosoft) فنجرب الاثنين
    text = text.replace("0", "o").replace("3", "e").replace("5", "s")
    text = text.replace("rn", "m").replace("vv", "w")
    return {text.replace("1", "l"), text.replace("1", "i")}


def looks_like_ip(host):
    # يكشف 1.2.3.4 و 2130706433 و 0x7f.0.0.1 و IPv6
    if re.fullmatch(r"(0x[0-9a-f]+|\d+)(\.(0x[0-9a-f]+|\d+))*", host):
        return True
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def safe_to_open(link):
    # نتأكد ان الموقع عام قبل لا نفتحه، عشان ما أحد يستخدم موقعي يفتح مواقع داخلية
    try:
        info = urlparse(link)
        if info.scheme not in ("http", "https") or not info.hostname:
            return False
        if info.port not in (None, 80, 443):
            return False
        # نفحص كل العناوين اللي يرجعها الدومين (IPv4 و IPv6) مو واحد بس
        for res in socket.getaddrinfo(info.hostname, info.port or 443,
                                      proto=socket.IPPROTO_TCP):
            ip = ipaddress.ip_address(res[4][0].split("%")[0])
            if not ip.is_global:
                return False
        return True
    except Exception:
        return False


def fetch_page(link):
    # نفتح الصفحة ونتبع التحويلات بنفسنا، وكل تحويل نفحصه بـ safe_to_open
    # نرجع: (الكود، الرابط النهائي، ملاحظة لو ما قدرنا)
    for _ in range(MAX_REDIRECTS + 1):
        if not safe_to_open(link):
            return None, link, "ما فتحت الصفحة (الموقع مو عام او مو مسموح)"
        with requests.get(link, timeout=5, allow_redirects=False,
                          headers=HEADERS, stream=True) as r:
            location = r.headers.get("Location")
            if r.status_code in (301, 302, 303, 307, 308) and location:
                link = urljoin(link, location)
                continue
            html = r.raw.read(200000, decode_content=True).decode("utf-8", errors="ignore")
            return html, link, None
    return None, link, "الرابط فيه تحويلات كثيرة"


# ---------- فحص الصفحة ----------

def check_html(html, site, is_https):
    # نفحص كود الصفحة، ونرجع النقاط والأسباب
    points = 0
    reasons = []
    html = html.lower()

    # هل فيها خانة كلمة سر
    if re.search(r'type\s*=\s*["\']?password', html):
        points += 1
        reasons.append("الصفحة فيها خانة كلمة سر")
        if not is_https:
            points += 2
            reasons.append("خانة كلمة السر بدون https")

    # هل الفورم يرسل البيانات لموقع ثاني
    actions = re.findall(r'<form[^>]*action\s*=\s*["\']([^"\']+)', html)
    for action in actions:
        action = action.strip()
        if action.startswith("//"):
            action = "https:" + action
        if action.startswith("http"):
            try:
                host = urlparse(action).hostname
            except ValueError:
                continue
            if host and not same_site(host, site):
                points += 2
                reasons.append("الفورم يرسل البيانات لموقع ثاني: " + host)
                break

    return points, reasons


def check_page(link, site):
    try:
        html, final_link, note = fetch_page(link)
    except requests.exceptions.SSLError:
        return 2, ["شهادة الموقع غير صالحة (منتهية او غير موثوقة)"]
    except requests.exceptions.Timeout:
        return 0, ["انتهت مهلة الاتصال، ما قدرت افحص محتوى الصفحة"]
    except requests.exceptions.RequestException:
        return 0, ["ما قدرت افتح الصفحة"]

    if html is None:
        return 0, [note]

    points = 0
    reasons = []

    # لو الرابط حوّلنا لموقع ثاني (مهم مع الروابط المختصرة)
    final_site = urlparse(final_link).hostname or ""
    if not same_site(final_site, site):
        points += 1
        reasons.append("الرابط يحوّل لموقع ثاني: " + final_site)

    p, r = check_html(html, final_site, final_link.startswith("https://"))
    return points + p, reasons + r


# ---------- فحص الرابط ----------

def check_link(raw):
    points = 0
    reasons = []

    raw = raw.strip()

    # لو الخانة فاضية
    if raw == "":
        return "اكتب رابط اول", 0, []
    if len(raw) > 2000:
        return "الرابط طويل جدا ما اقدر افحصه", 0, []

    # لو المستخدم ما كتب http او https نفترض https (ونفتحه من الاصل بنفس الرابط)
    if not re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.I):
        raw = "https://" + raw

    link = raw              # الاصلي (نستخدمه للفتح لان المسارات تفرق بين الكبير والصغير)
    low = link.lower()      # للتحليل

    # نطلع اسم الموقع بـ urlparse (اضبط من القص اليدوي)
    try:
        info = urlparse(link)
        site = info.hostname or ""     # يشيل @ والبورت ويصغر الحروف
        _ = info.port                  # يرمي ValueError لو البورت غلط
    except ValueError:
        return "رابط غير صالح", 0, ["الرابط مكتوب بشكل غير صحيح"]
    if site == "":
        return "رابط غير صالح", 0, ["ما قدرت اطلع اسم الموقع من الرابط"]

    # 1) هل الرابط بدون https
    if not low.startswith("https://"):
        points += 1
        reasons.append("الرابط لا يستخدم https")

    # 2) هل الرابط طويل
    if len(low) > 75:
        points += 1
        reasons.append("الرابط طويل جدا")

    # 3) هل فيه علامة @ قبل اسم الموقع (مو في المسار: youtube.com/@name عادي)
    if "@" in info.netloc:
        points += 3
        reasons.append("الرابط فيه علامة @ قبل اسم الموقع")

    # 4) هل فيه رقم IP بدل اسم الموقع
    if looks_like_ip(site):
        points += 3
        reasons.append("الرابط يستخدم رقم IP")

    # 5) هل فيه شرطات كثيرة
    if site.replace("xn--", "").count("-") >= 2:   # xn-- ما تنحسب شرطات
        points += 1
        reasons.append("اسم الموقع فيه شرطات كثيرة")

    # 6) هل فيه نقاط كثيرة (مواقع فرعية كثيرة)
    if site.count(".") > 3:
        points += 1
        reasons.append("اسم الموقع فيه نقاط كثيرة")

    # 7) هل ينتهي بامتداد مشبوه
    for end in bad_endings:
        if site.endswith(end):
            points += 2
            reasons.append("امتداد الموقع مشبوه " + end)

    # 8) هل هو رابط مختصر
    if strip_www(site) in short_links:
        points += 2
        reasons.append("رابط مختصر")

    # هل هو موقع حقيقي لشركة معروفة (accounts.google.com مثلا)
    trusted_site = any(is_real(site, domains) for domains in brands.values())

    # 9) كلمات مشبوهة (نتجاهلها للمواقع الحقيقية المعروفة)
    if not trusted_site:
        count = 0
        for word in bad_words:
            if word in low:
                count += 1
        if count >= 2:
            points += 2
            reasons.append("فيه كلمات مشبوهة")

    # 10) هل ينتحل اسم شركة معروفة (او يقلده بتغيير حرف)
    parts = site.replace("-", ".").split(".")
    clean_parts = set()
    for variant in clean_variants(site):
        clean_parts.update(variant.replace("-", ".").split("."))
    for brand, domains in brands.items():
        if brand in parts:
            if not is_real(site, domains):
                points += 3
                reasons.append("ينتحل اسم شركة معروفة: " + brand)
        elif brand in clean_parts:
            points += 3
            reasons.append("اسم الموقع يقلد شركة معروفة: " + brand)

    # 11) هل فيه حروف غريبة (punycode)
    if "xn--" in site:
        points += 2
        reasons.append("اسم الموقع فيه حروف غريبة تقلد حروف ثانية")

    # 12) نفحص محتوى الصفحة
    page_points, page_reasons = check_page(link, site)
    points += page_points
    reasons += page_reasons

    # النتيجة النهائية
    # ملاحظة: ما نقول "موثوق" لان عدم وجود مؤشرات ما يعني انه امن 100%
    if points <= 1:
        result = "لم نرصد مؤشرات خطر"
    elif points <= 3:
        result = "مشبوه"
    else:
        result = "خطير"

    return result, points, reasons


page = """
<html dir="rtl" lang="ar">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>فاحص الروابط</title>
</head>
<body style="font-family: Arial; max-width: 600px; margin: 30px auto; padding: 0 15px;">
  <h2>فاحص الروابط</h2>
  <form method="post">
    <input name="link" value="{{ link }}" placeholder="الصق الرابط هنا"
           style="width:100%; padding:10px; direction:ltr;">
    <button type="submit" style="margin-top:10px; padding:10px; width:100%;">افحص</button>
  </form>

  {% if result %}
    <h3>النتيجة: {{ result }}</h3>
    <p>نقاط الخطر: {{ points }}</p>
    <ul>
      {% for r in reasons %}
        <li>{{ r }}</li>
      {% endfor %}
    </ul>
  {% endif %}
</body>
</html>
"""


@app.route("/", methods=["GET", "POST"])
def home():
    link = ""
    result = None
    points = 0
    reasons = []

    if request.method == "POST":
        link = request.form.get("link", "")
        result, points, reasons = check_link(link)

    return render_template_string(page, link=link, result=result, points=points, reasons=reasons)


if __name__ == "__main__":
    # debug=True يفتح كونسول يقدر ينفذ اوامر على جهازك، لا تستخدمه الا محليا وانت تطور
    app.run(debug=False)
