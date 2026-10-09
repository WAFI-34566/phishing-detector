# مشروع التخرج: موقع لفحص الروابط (موثوق أو مشبوه)
# التثبيت: pip install flask requests
# التشغيل: python link_checker.py ثم افتح http://localhost:5000

import re
import socket
import ipaddress
import requests
from urllib.parse import urlparse
from flask import Flask, request, render_template_string

app = Flask(__name__)

# قوائم الكلمات المشبوهة
bad_words = ["login", "signin", "verify", "update", "secure", "account", "password",
             "bank", "free", "gift", "confirm", "wallet", "billing", "suspended"]
bad_endings = [".xyz", ".top", ".tk", ".ml", ".ga", ".cf", ".click", ".icu", ".buzz", ".work"]
short_links = ["bit.ly", "tinyurl.com", "t.co", "cutt.ly", "is.gd", "rb.gy", "ow.ly", "goo.gl"]

# الشركات المعروفة ومواقعها الحقيقية (عشان نكشف اللي ينتحل اسمها)
brands = {
    "paypal": ["paypal.com"],
    "google": ["google.com"],
    "apple": ["apple.com"],
    "amazon": ["amazon.com", "amazon.sa"],
    "microsoft": ["microsoft.com"],
    "facebook": ["facebook.com"],
    "netflix": ["netflix.com"],
    "whatsapp": ["whatsapp.com"],
    "stc": ["stc.com.sa"],
    "absher": ["absher.sa", "moi.gov.sa"],
    "alrajhibank": ["alrajhibank.com.sa"],
}


def clean_name(text):
    # نرجع الحروف المقلدة لأصلها: paypa1 تصير paypal و g00gle تصير google
    text = text.replace("0", "o").replace("1", "l").replace("3", "e").replace("5", "s")
    text = text.replace("rn", "m")
    return text


def safe_to_open(link):
    # نتأكد ان الموقع عام قبل لا نفتحه، عشان ما أحد يستخدم موقعي يفتح مواقع داخلية
    info = urlparse(link)
    if info.scheme not in ("http", "https"):
        return False
    if info.port not in (None, 80, 443):
        return False
    try:
        ip = ipaddress.ip_address(socket.gethostbyname(info.hostname))
    except Exception:
        return False
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
        return False
    return True


def check_html(html, site, is_https):
    # نفحص كود الصفحة، ونرجع النقاط والأسباب
    points = 0
    reasons = []
    html = html.lower()

    # هل فيها خانة كلمة سر
    if 'type="password"' in html or "type='password'" in html:
        points += 1
        reasons.append("الصفحة فيها خانة كلمة سر")
        if not is_https:
            points += 2
            reasons.append("خانة كلمة السر بدون https")

    # هل الفورم يرسل البيانات لموقع ثاني
    actions = re.findall(r'<form[^>]*action=["\']([^"\']+)', html)
    for action in actions:
        if action.startswith("http"):
            host = urlparse(action).hostname
            if host != site:
                points += 2
                reasons.append("الفورم يرسل البيانات لموقع ثاني: " + str(host))
                break

    return points, reasons


def check_page(link, site, is_https):
    # نفتح الصفحة (بدون تحويلات) ونقرأ اول 200 الف حرف بس
    if not link.startswith("http"):
        link = "http://" + link
    if not safe_to_open(link):
        return 0, ["ما فتحت الصفحة (الموقع مو عام او مو مسموح)"]
    try:
        r = requests.get(link, timeout=5, allow_redirects=False,
                         headers={"User-Agent": "Mozilla/5.0"}, stream=True)
        html = r.raw.read(200000, decode_content=True).decode("utf-8", errors="ignore")
    except Exception:
        return 0, ["ما قدرت افتح الصفحة"]
    return check_html(html, site, is_https)


def check_link(link):
    points = 0
    reasons = []

    link = link.lower().strip()

    # لو الخانة فاضية
    if link == "":
        return "اكتب رابط اول", 0, []

    # 1) هل الرابط بدون https
    if not link.startswith("https://"):
        points += 1
        reasons.append("الرابط لا يستخدم https")

    # 2) هل الرابط طويل
    if len(link) > 75:
        points += 1
        reasons.append("الرابط طويل جدا")

    # 3) هل فيه علامة @
    if "@" in link:
        points += 3
        reasons.append("الرابط فيه علامة @")

    # نطلع اسم الموقع من الرابط
    site = link.replace("https://", "").replace("http://", "").split("/")[0]
    if "@" in site:
        site = site.split("@")[-1]   # نخذ اللي بعد @ لانه الموقع الحقيقي
    site = site.split(":")[0]        # نشيل رقم البورت لو موجود

    # 4) هل فيه رقم IP بدل اسم الموقع
    if site.replace(".", "").isdigit():
        points += 3
        reasons.append("الرابط يستخدم رقم IP")

    # 5) هل فيه شرطات كثيرة
    if site.count("-") >= 2:
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
    if site in short_links:
        points += 2
        reasons.append("رابط مختصر")

    # 9) كلمات مشبوهة
    count = 0
    for word in bad_words:
        if word in link:
            count += 1
    if count >= 2:
        points += 2
        reasons.append("فيه كلمات مشبوهة")

    # 10) هل ينتحل اسم شركة معروفة (او يقلده بتغيير حرف)
    parts = site.replace("-", ".").split(".")
    clean_parts = clean_name(site).replace("-", ".").split(".")
    for brand in brands:
        if brand in parts:
            real = False
            for domain in brands[brand]:
                if site == domain or site.endswith("." + domain):
                    real = True
            if not real:
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
    page_points, page_reasons = check_page(link, site, link.startswith("https://"))
    points += page_points
    reasons += page_reasons

    # النتيجة النهائية
    if points <= 1:
        result = "يبدو موثوق"
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
        link = request.form["link"]
        result, points, reasons = check_link(link)

    return render_template_string(page, link=link, result=result, points=points, reasons=reasons)


if __name__ == "__main__":
    app.run(debug=True)