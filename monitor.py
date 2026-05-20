import requests
import hashlib
import json
import time
import re
import schedule
import logging
import os
from datetime import datetime
from bs4 import BeautifulSoup

# ================================================================
#   НАСТРОЙКИ
# ================================================================
TELEGRAM_TOKEN   = "8927480306:AAEf373-88rkkC3PBc4vHLIwEOkLdbddXXI"    # @BotFather → /newbot
TELEGRAM_CHAT_ID = "8104157465"       # @userinfobot → /start

# ── Ключевые слова (RU + EN) ──────────────────────────────────
KEYWORDS_RU = [
    "сайт", "лендинг", "верстка", "вёрстка", "разработка",
    "wordpress", "bitrix", "modx", "react", "php", "python",
    "интернет-магазин", "веб", "frontend", "backend", "fullstack",
    "html", "css", "javascript", "typescript", "cms",
    "парсер", "парсинг", "бот", "телеграм бот", "автоматизация",
    "vue", "next.js", "nuxt", "rest api", "интеграция"
]
KEYWORDS_EN = [
    "website", "landing page", "web development", "wordpress",
    "react", "php", "python", "frontend", "backend", "fullstack",
    "html", "css", "javascript", "typescript", "web design",
    "ecommerce", "shopify", "woocommerce", "api integration",
    "bot", "automation", "parser", "scraper", "vue", "next.js",
    "laravel", "django", "node.js", "rest api", "cms"
]
KEYWORDS = KEYWORDS_RU + KEYWORDS_EN

# ── Стоп-слова ────────────────────────────────────────────────
STOP_WORDS = [
    # офис
    "работа в офисе", "в офисе обязательно", "только офис",
    "требуется присутствие", "приезжать в офис", "очная работа",
    # не наша тема
    "грузчик", "водитель", "курьер", "уборщик", "охранник",
    "продавец", "кассир", "монтажник", "сварщик", "разнорабочий",
    "пятидневка", "график 5/2",
    # EN
    "on-site only", "must be local", "in-office", "on site required",
]

# ── Удалёнка ──────────────────────────────────────────────────
REMOTE_KEYWORDS = [
    "удалённо", "удаленно", "удалёнка", "дистанционно",
    "из любой точки", "любой город", "без офиса",
    "remote", "work from home", "wfh", "anywhere", "online",
    "home office", "fully remote", "100% remote",
]

MIN_BUDGET             = 300   # ₽ или $ — просто отсекает совсем копейки
CHECK_INTERVAL_MINUTES = 60    # проверка раз в час (15 сайтов — не спамим)
# ================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("monitor.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

SEEN_FILE = "seen_orders.json"

HEADERS_RU = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9",
}
HEADERS_EN = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}


# ────────────────────────────────────────────
#   Хранилище
# ────────────────────────────────────────────
def load_seen() -> set:
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()

def save_seen(seen: set):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen)[-5000:], f)  # храним последние 5000, не растёт бесконечно

def make_id(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()

def safe_get(url, headers=None, timeout=15, retries=2) -> requests.Response | None:
    """GET с повторными попытками"""
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=headers or HEADERS_EN, timeout=timeout)
            if r.status_code == 200:
                return r
            log.debug(f"HTTP {r.status_code} для {url}")
        except Exception as e:
            log.debug(f"Попытка {attempt+1} неудачна для {url}: {e}")
            time.sleep(2)
    return None


# ────────────────────────────────────────────
#   Telegram
# ────────────────────────────────────────────
def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }, timeout=10)
        if r.status_code != 200:
            log.error(f"Telegram error: {r.text}")
    except Exception as e:
        log.error(f"Telegram failed: {e}")

def format_order(source: str, title: str, url: str,
                 budget="", description="", matched_kw=None, flag="🇷🇺"):
    budget_str = f"\n💰 <b>Бюджет:</b> {budget}" if budget else ""
    desc_cut   = description[:200] + "..." if len(description) > 200 else description
    desc_str   = f"\n📝 {desc_cut}" if desc_cut else ""
    kw_str     = f"\n🏷 <i>{', '.join(matched_kw[:4])}</i>" if matched_kw else ""
    return (
        f"🔔 {flag} <b>{source}</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📌 <b>{title}</b>"
        f"{budget_str}"
        f"{desc_str}"
        f"{kw_str}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🔗 <a href='{url}'>Открыть заказ →</a>\n"
        f"⏰ {datetime.now().strftime('%d.%m %H:%M')}"
    )


# ────────────────────────────────────────────
#   Умный фильтр
# ────────────────────────────────────────────
def extract_budget(text: str) -> int:
    for n in re.findall(r'\d[\d\s]{1,8}\d', text.replace('\xa0', ' ').replace(',', '')):
        try:
            val = int(n.replace(' ', ''))
            if 100 <= val <= 10_000_000:
                return val
        except:
            pass
    return 0

def is_relevant(title: str, desc: str = "", budget_str: str = ""):
    text = (title + " " + desc).lower()

    matched = [kw for kw in KEYWORDS if kw.lower() in text]
    if not matched:
        return False, "не наша тематика"

    for sw in STOP_WORDS:
        if sw.lower() in text:
            return False, f"стоп «{sw}»"

    has_office = any(w in text for w in ["в офисе", "on-site only", "in-office", "приезжать"])
    has_remote = any(w in text for w in [r.lower() for r in REMOTE_KEYWORDS])
    if has_office and not has_remote:
        return False, "офис без удалёнки"

    if MIN_BUDGET > 0 and budget_str:
        val = extract_budget(budget_str)
        if 0 < val < MIN_BUDGET:
            return False, f"бюджет {val} < мин {MIN_BUDGET}"

    return True, matched


# ════════════════════════════════════════════
#   🇷🇺 РУССКИЕ ПЛОЩАДКИ
# ════════════════════════════════════════════

def parse_fl_ru(seen):
    found = []
    try:
        r = safe_get("https://www.fl.ru/projects/?kind=1", HEADERS_RU)
        if not r: return found
        soup = BeautifulSoup(r.text, "html.parser")
        for item in soup.select("div.b-post.b-post_project")[:30]:
            title_el = item.select_one("h2.b-post__title a")
            if not title_el: continue
            title  = title_el.get_text(strip=True)
            link   = "https://www.fl.ru" + title_el.get("href", "")
            desc   = (item.select_one("div.b-post__body") or item).get_text(strip=True)
            b_el   = item.select_one("span.b-post__price")
            budget = b_el.get_text(strip=True) if b_el else ""
            oid = make_id(link)
            if oid in seen: continue
            ok, res = is_relevant(title, desc, budget)
            if not ok: continue
            seen.add(oid)
            found.append(format_order("FL.ru", title, link, budget, desc, res, "🇷🇺"))
            log.info(f"[FL.ru] ✅ {title[:55]}")
    except Exception as e:
        log.error(f"FL.ru: {e}")
    return found


def parse_kwork(seen):
    found = []
    for cat, name in [("41", "Сайты"), ("45", "Боты"), ("46", "Парсинг"), ("15", "Программирование")]:
        try:
            r = safe_get(f"https://kwork.ru/projects?c={cat}", HEADERS_RU)
            if not r: continue
            soup = BeautifulSoup(r.text, "html.parser")
            for item in soup.select("div.want-card")[:20]:
                title_el = item.select_one("a.want-card__title-link")
                if not title_el: continue
                title  = title_el.get_text(strip=True)
                link   = "https://kwork.ru" + title_el.get("href", "")
                desc   = (item.select_one("div.want-card__description") or item).get_text(strip=True)
                b_el   = item.select_one("div.want-card__price")
                budget = b_el.get_text(strip=True) if b_el else ""
                oid = make_id(link)
                if oid in seen: continue
                ok, res = is_relevant(title, desc, budget)
                if not ok: continue
                seen.add(oid)
                found.append(format_order("Kwork", title, link, budget, desc, res, "🇷🇺"))
                log.info(f"[Kwork/{name}] ✅ {title[:55]}")
            time.sleep(1)
        except Exception as e:
            log.error(f"Kwork cat={cat}: {e}")
    return found


def parse_habr(seen):
    found = []
    for q in ["сайт разработка", "верстка react", "php wordpress", "python бот"]:
        try:
            url = f"https://freelance.habr.com/tasks.rss?q={requests.utils.quote(q)}&categories=development"
            r   = safe_get(url, HEADERS_RU)
            if not r: continue
            soup = BeautifulSoup(r.text, "xml")
            for item in soup.select("item")[:15]:
                title = (item.find("title") or item).get_text(strip=True)
                link  = (item.find("link")  or item).get_text(strip=True)
                raw   = (item.find("description") or item).get_text(strip=True)
                desc  = BeautifulSoup(raw, "html.parser").get_text(strip=True)
                oid = make_id(link)
                if oid in seen: continue
                ok, res = is_relevant(title, desc)
                if not ok: continue
                seen.add(oid)
                found.append(format_order("Habr Freelance", title, link, "", desc, res, "🇷🇺"))
                log.info(f"[Habr] ✅ {title[:55]}")
            time.sleep(1)
        except Exception as e:
            log.error(f"Habr q={q}: {e}")
    return found


def parse_youdo(seen):
    """YouDo — раздел IT и разработка"""
    found = []
    try:
        r = safe_get("https://youdo.com/t-programmirovanie-saytov", HEADERS_RU)
        if not r: return found
        soup = BeautifulSoup(r.text, "html.parser")
        for item in soup.select("li.task-item, div.task-list__item")[:20]:
            title_el = item.select_one("a.title, h3 a, a.task-title")
            if not title_el: continue
            title = title_el.get_text(strip=True)
            href  = title_el.get("href", "")
            link  = ("https://youdo.com" + href) if href.startswith("/") else href
            desc  = (item.select_one("p.description, p.task-description") or item).get_text(strip=True)
            b_el  = item.select_one("span.price, div.price")
            budget = b_el.get_text(strip=True) if b_el else ""
            oid = make_id(link)
            if oid in seen: continue
            ok, res = is_relevant(title, desc, budget)
            if not ok: continue
            seen.add(oid)
            found.append(format_order("YouDo", title, link, budget, desc, res, "🇷🇺"))
            log.info(f"[YouDo] ✅ {title[:55]}")
    except Exception as e:
        log.error(f"YouDo: {e}")
    return found


def parse_avito(seen):
    found = []
    try:
        url = ("https://www.avito.ru/rossiya/predlozheniya_uslug/"
               "razrabotka_saytov-ASgBAgICAUSUA9AQ")
        r = safe_get(url, HEADERS_RU)
        if not r: return found
        soup = BeautifulSoup(r.text, "html.parser")
        for item in soup.select("[data-marker='item']")[:15]:
            title_el = item.select_one("[itemprop='name']") or item.select_one("h3")
            link_el  = item.select_one("a[href]")
            price_el = item.select_one("meta[itemprop='price']")
            if not title_el or not link_el: continue
            title  = title_el.get_text(strip=True)
            link   = "https://www.avito.ru" + link_el.get("href", "")
            budget = (price_el.get("content", "") + " ₽") if price_el else ""
            oid = make_id(link)
            if oid in seen: continue
            ok, res = is_relevant(title, "", budget)
            if not ok: continue
            seen.add(oid)
            found.append(format_order("Авито", title, link, budget, "", res, "🇷🇺"))
            log.info(f"[Авито] ✅ {title[:55]}")
    except Exception as e:
        log.error(f"Avito: {e}")
    return found


def parse_weblancer(seen):
    """Weblancer.net — специализированная биржа для веб"""
    found = []
    try:
        r = safe_get("https://www.weblancer.net/jobs/?group=10", HEADERS_RU)
        if not r: return found
        soup = BeautifulSoup(r.text, "html.parser")
        for item in soup.select("div.title_wrapper, tr.item")[:20]:
            title_el = item.select_one("a.title, h2 a")
            if not title_el: continue
            title = title_el.get_text(strip=True)
            href  = title_el.get("href", "")
            link  = ("https://www.weblancer.net" + href) if href.startswith("/") else href
            b_el  = item.select_one("span.cost, div.cost")
            budget = b_el.get_text(strip=True) if b_el else ""
            oid = make_id(link)
            if oid in seen: continue
            ok, res = is_relevant(title, "", budget)
            if not ok: continue
            seen.add(oid)
            found.append(format_order("Weblancer", title, link, budget, "", res, "🇷🇺"))
            log.info(f"[Weblancer] ✅ {title[:55]}")
    except Exception as e:
        log.error(f"Weblancer: {e}")
    return found


def parse_freelansim(seen):
    """Freelansim.ru — RSS лента"""
    found = []
    try:
        r = safe_get("https://freelansim.ru/tasks.rss", HEADERS_RU)
        if not r: return found
        soup = BeautifulSoup(r.text, "xml")
        for item in soup.select("item")[:20]:
            title = (item.find("title") or item).get_text(strip=True)
            link  = (item.find("link")  or item).get_text(strip=True)
            raw   = (item.find("description") or item).get_text(strip=True)
            desc  = BeautifulSoup(raw, "html.parser").get_text(strip=True)
            oid = make_id(link)
            if oid in seen: continue
            ok, res = is_relevant(title, desc)
            if not ok: continue
            seen.add(oid)
            found.append(format_order("Freelansim", title, link, "", desc, res, "🇷🇺"))
            log.info(f"[Freelansim] ✅ {title[:55]}")
    except Exception as e:
        log.error(f"Freelansim: {e}")
    return found


# ════════════════════════════════════════════
#   🌍 ЗАРУБЕЖНЫЕ ПЛОЩАДКИ
# ════════════════════════════════════════════

def parse_upwork_rss(seen):
    """Upwork — публичный RSS поиск (без авторизации)"""
    found = []
    queries = [
        "web development",
        "wordpress developer",
        "react frontend",
        "php developer",
        "landing page",
    ]
    for q in queries:
        try:
            url = f"https://www.upwork.com/ab/feed/jobs/rss?q={requests.utils.quote(q)}&sort=recency&paging=0%3B10"
            r   = safe_get(url, HEADERS_EN)
            if not r: continue
            soup = BeautifulSoup(r.text, "xml")
            for item in soup.select("item")[:10]:
                title = (item.find("title") or item).get_text(strip=True)
                link  = (item.find("link")  or item).get_text(strip=True)
                raw   = (item.find("description") or item).get_text(strip=True)
                desc  = BeautifulSoup(raw, "html.parser").get_text(strip=True)
                # бюджет часто в описании: "$500" 
                budget_match = re.search(r'\$[\d,]+', desc)
                budget = budget_match.group(0) if budget_match else ""
                oid = make_id(link)
                if oid in seen: continue
                ok, res = is_relevant(title, desc, budget)
                if not ok: continue
                seen.add(oid)
                found.append(format_order("Upwork", title, link, budget, desc, res, "🌍"))
                log.info(f"[Upwork] ✅ {title[:55]}")
            time.sleep(1.5)
        except Exception as e:
            log.error(f"Upwork q={q}: {e}")
    return found


def parse_freelancer_com(seen):
    """Freelancer.com — публичный API проектов"""
    found = []
    try:
        # Открытый API без ключа — возвращает последние проекты
        url = ("https://www.freelancer.com/api/projects/0.1/projects/active/"
               "?job_details=true&limit=20&offset=0"
               "&jobs[]=web-design&jobs[]=php&jobs[]=javascript&jobs[]=html")
        r = safe_get(url, HEADERS_EN)
        if not r: return found
        data = r.json()
        projects = data.get("result", {}).get("projects", [])
        for p in projects:
            title  = p.get("title", "")
            pid    = p.get("id", "")
            link   = f"https://www.freelancer.com/projects/{p.get('seo_url', pid)}"
            desc   = p.get("description", "")
            budget = ""
            b = p.get("budget", {})
            if b:
                mn = b.get("minimum", "")
                mx = b.get("maximum", "")
                budget = f"${mn}–${mx}" if mn and mx else f"${mn or mx}"
            oid = make_id(str(pid))
            if oid in seen: continue
            ok, res = is_relevant(title, desc, budget)
            if not ok: continue
            seen.add(oid)
            found.append(format_order("Freelancer.com", title, link, budget, desc, res, "🌍"))
            log.info(f"[Freelancer.com] ✅ {title[:55]}")
    except Exception as e:
        log.error(f"Freelancer.com: {e}")
    return found


def parse_guru(seen):
    """Guru.com — RSS публичных проектов"""
    found = []
    try:
        url = "https://www.guru.com/d/jobs/cat/programming-development/pg/1/?format=rss"
        r   = safe_get(url, HEADERS_EN)
        if not r: return found
        soup = BeautifulSoup(r.text, "xml")
        for item in soup.select("item")[:15]:
            title = (item.find("title") or item).get_text(strip=True)
            link  = (item.find("link")  or item).get_text(strip=True)
            raw   = (item.find("description") or item).get_text(strip=True)
            desc  = BeautifulSoup(raw, "html.parser").get_text(strip=True)
            oid = make_id(link)
            if oid in seen: continue
            ok, res = is_relevant(title, desc)
            if not ok: continue
            seen.add(oid)
            found.append(format_order("Guru.com", title, link, "", desc, res, "🌍"))
            log.info(f"[Guru] ✅ {title[:55]}")
    except Exception as e:
        log.error(f"Guru: {e}")
    return found


def parse_peopleperhour(seen):
    """PeoplePerHour — публичный поиск проектов"""
    found = []
    try:
        url = "https://www.peopleperhour.com/freelance-jobs/technology-programming?ref=nav"
        r   = safe_get(url, HEADERS_EN)
        if not r: return found
        soup = BeautifulSoup(r.text, "html.parser")
        for item in soup.select("li.listings-item, div.project-item")[:15]:
            title_el = item.select_one("a.listings-item__title, h2 a, a.title")
            if not title_el: continue
            title = title_el.get_text(strip=True)
            href  = title_el.get("href", "")
            link  = href if href.startswith("http") else "https://www.peopleperhour.com" + href
            desc  = (item.select_one("p.listings-item__description, p.description") or item).get_text(strip=True)
            b_el  = item.select_one("span.price, div.budget")
            budget = b_el.get_text(strip=True) if b_el else ""
            oid = make_id(link)
            if oid in seen: continue
            ok, res = is_relevant(title, desc, budget)
            if not ok: continue
            seen.add(oid)
            found.append(format_order("PeoplePerHour", title, link, budget, desc, res, "🌍"))
            log.info(f"[PPH] ✅ {title[:55]}")
    except Exception as e:
        log.error(f"PeoplePerHour: {e}")
    return found


def parse_toptal_rss(seen):
    """Toptal blog/jobs RSS — высокооплачиваемые проекты"""
    found = []
    try:
        # Toptal публичный RSS
        url = "https://www.toptal.com/blog/rss/"
        r   = safe_get(url, HEADERS_EN)
        if not r: return found
        soup = BeautifulSoup(r.text, "xml")
        for item in soup.select("item")[:10]:
            title = (item.find("title") or item).get_text(strip=True)
            link  = (item.find("link")  or item).get_text(strip=True)
            raw   = (item.find("description") or item).get_text(strip=True)
            desc  = BeautifulSoup(raw, "html.parser").get_text(strip=True)
            oid = make_id(link)
            if oid in seen: continue
            ok, res = is_relevant(title, desc)
            if not ok: continue
            seen.add(oid)
            found.append(format_order("Toptal", title, link, "", desc, res, "🌍"))
            log.info(f"[Toptal] ✅ {title[:55]}")
    except Exception as e:
        log.error(f"Toptal: {e}")
    return found


def parse_remoteok(seen):
    """Remote OK — публичный JSON API, только remote вакансии"""
    found = []
    try:
        r = safe_get("https://remoteok.com/api?tag=dev", HEADERS_EN)
        if not r: return found
        jobs = r.json()
        for job in jobs[1:25]:  # первый элемент — мета
            if not isinstance(job, dict): continue
            title  = job.get("position", "")
            link   = job.get("url", "")
            desc   = job.get("description", "")
            tags   = " ".join(job.get("tags", []))
            budget = job.get("salary", "") or ""
            if not title or not link: continue
            oid = make_id(link)
            if oid in seen: continue
            ok, res = is_relevant(title, desc + " " + tags, str(budget))
            if not ok: continue
            seen.add(oid)
            found.append(format_order("RemoteOK", title, link, str(budget), desc[:200], res, "🌍"))
            log.info(f"[RemoteOK] ✅ {title[:55]}")
    except Exception as e:
        log.error(f"RemoteOK: {e}")
    return found


def parse_weworkremotely(seen):
    """We Work Remotely — RSS только remote работа"""
    found = []
    try:
        url = "https://weworkremotely.com/categories/remote-programming-jobs.rss"
        r   = safe_get(url, HEADERS_EN)
        if not r: return found
        soup = BeautifulSoup(r.text, "xml")
        for item in soup.select("item")[:15]:
            title = (item.find("title") or item).get_text(strip=True)
            link  = (item.find("link")  or item).get_text(strip=True)
            raw   = (item.find("description") or item).get_text(strip=True)
            desc  = BeautifulSoup(raw, "html.parser").get_text(strip=True)
            oid = make_id(link)
            if oid in seen: continue
            ok, res = is_relevant(title, desc)
            if not ok: continue
            seen.add(oid)
            found.append(format_order("WeWorkRemotely", title, link, "", desc, res, "🌍"))
            log.info(f"[WWR] ✅ {title[:55]}")
    except Exception as e:
        log.error(f"WWR: {e}")
    return found


def parse_workingnomads(seen):
    """Working Nomads — JSON API remote вакансий"""
    found = []
    try:
        url = "https://www.workingnomads.com/api/exposed_jobs/?category=development"
        r   = safe_get(url, HEADERS_EN)
        if not r: return found
        jobs = r.json()
        for job in jobs[:20]:
            if not isinstance(job, dict): continue
            title = job.get("title", "")
            link  = job.get("url", "") or job.get("apply_url", "")
            desc  = job.get("description", "") or job.get("excerpt", "")
            tags  = " ".join(job.get("tags", []))
            if not title: continue
            oid = make_id(title + link)
            if oid in seen: continue
            ok, res = is_relevant(title, desc + " " + tags)
            if not ok: continue
            seen.add(oid)
            found.append(format_order("WorkingNomads", title, link, "", desc[:200], res, "🌍"))
            log.info(f"[WorkingNomads] ✅ {title[:55]}")
    except Exception as e:
        log.error(f"WorkingNomads: {e}")
    return found


# ════════════════════════════════════════════
#   Главный цикл
# ════════════════════════════════════════════

# Все парсеры с паузами между ними
PARSERS = [
    ("FL.ru",           parse_fl_ru,          2),
    ("Kwork",           parse_kwork,          2),
    ("Habr Freelance",  parse_habr,           2),
    ("YouDo",           parse_youdo,          2),
    ("Авито",           parse_avito,          2),
    ("Weblancer",       parse_weblancer,      2),
    ("Freelansim",      parse_freelansim,     2),
    ("Upwork",          parse_upwork_rss,     3),
    ("Freelancer.com",  parse_freelancer_com, 3),
    ("Guru.com",        parse_guru,           2),
    ("PeoplePerHour",   parse_peopleperhour,  2),
    ("RemoteOK",        parse_remoteok,       2),
    ("WeWorkRemotely",  parse_weworkremotely, 2),
    ("WorkingNomads",   parse_workingnomads,  2),
    ("Toptal",          parse_toptal_rss,     2),
]

def check_all():
    log.info(f"🔍 Запуск проверки {len(PARSERS)} площадок...")
    seen  = load_seen()
    found = []
    ok_count  = 0
    err_count = 0

    for name, parser, pause in PARSERS:
        try:
            results = parser(seen)
            found  += results
            ok_count += 1
            log.info(f"[{name}] готово, найдено: {len(results)}")
        except Exception as e:
            err_count += 1
            log.error(f"[{name}] ошибка парсера: {e}")
        time.sleep(pause)

    save_seen(seen)

    if found:
        log.info(f"✅ Всего новых подходящих заказов: {len(found)}")
        # Сначала шлём сводку
        send_telegram(
            f"📊 <b>Найдено {len(found)} новых заказов</b>\n"
            f"Площадок проверено: {ok_count}/{len(PARSERS)}\n"
            f"⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        time.sleep(1)
        for msg in found:
            send_telegram(msg)
            time.sleep(1.5)
    else:
        log.info("Новых подходящих заказов нет")


def main():
    log.info(f"🚀 Монитор запущен | {len(PARSERS)} площадок")
    send_telegram(
        f"✅ <b>Монитор заказов запущен!</b>\n\n"
        f"🇷🇺 RU: FL.ru · Kwork · Habr · YouDo · Авито · Weblancer · Freelansim\n"
        f"🌍 EN: Upwork · Freelancer.com · Guru · PeoplePerHour · RemoteOK · WeWorkRemotely · WorkingNomads · Toptal\n\n"
        f"🎯 Фильтр: удалёнка + наша тематика + мин {MIN_BUDGET}₽/$\n"
        f"⏱ Интервал: каждые {CHECK_INTERVAL_MINUTES} мин"
    )
    check_all()
    schedule.every(CHECK_INTERVAL_MINUTES).minutes.do(check_all)
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
