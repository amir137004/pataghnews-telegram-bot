import os
import json
import re
import time
import hashlib
import requests
import feedparser

from bs4 import BeautifulSoup
from urllib.parse import urljoin

# تنظیمات
SITE_URL = "https://pataghnews.ir"
FEED_URL = SITE_URL + "/feed/"

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
STATE_FILE = "seen_urls.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; PataghNewsBot/1.0)"
}


def load_seen():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_seen(seen):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen)[-2000:], f, ensure_ascii=False, indent=2)


def clean_text(text):
    text = BeautifulSoup(text or "", "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def get_article_data(url, entry):
    title = clean_text(entry.get("title", ""))
    lead = clean_text(
        entry.get("summary", "") or entry.get("description", "")
    )

    image_url = None

    # بررسی تصویر موجود در RSS
    for item in entry.get("media_content", []):
        if item.get("url"):
            image_url = item["url"]
            break

    if not image_url:
        for item in entry.get("links", []):
            if item.get("rel") == "enclosure":
                if item.get("type", "").startswith("image/"):
                    image_url = item.get("href")
                    break

    # دریافت صفحه خبر برای استخراج لید و تصویر اصلی
    try:
        response = requests.get(
            url, headers=HEADERS, timeout=20
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            title = clean_text(og_title["content"]) or title

        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            image_url = urljoin(url, og_image["content"])

        # استخراج لید از توضیحات صفحه
        description = soup.find(
            "meta", attrs={"name": "description"}
        )

        if description and description.get("content"):
            lead = clean_text(description["content"])

        # اگر توضیحات متا موجود نبود، از خلاصه RSS استفاده می‌شود
        if not lead:
            lead = clean_text(entry.get("summary", ""))

    except requests.RequestException as exc:
        print(f"خطا در دریافت خبر {url}: {exc}")

    # محدودیت کپشن تلگرام
    lead = lead[:650].strip()

    return title, lead, image_url


def telegram_request(method, data, files=None):
    response = requests.post(
        f"{TELEGRAM_API}/{method}",
        data=data,
        files=files,
        timeout=60
    )
    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(str(result))

    return result


def publish_news(title, lead, image_url, url):
    caption = (
        f"📰 <b>{title}</b>\n\n"
        f"🔹 {lead}\n\n"
        f'🔗 <a href="{url}">متن کامل خبر در پاتاق نیوز</a>'
    )

    # تصویر اصلی خبر در همان پست منتشر می‌شود
    if image_url:
        try:
            image_response = requests.get(
                image_url, headers=HEADERS, timeout=30
            )
            image_response.raise_for_status()

            content_type = image_response.headers.get(
                "Content-Type", ""
            )

            if content_type.startswith("image/"):
                telegram_request(
                    "sendPhoto",
                    {
                        "chat_id": CHANNEL_ID,
                        "caption": caption[:1024],
                        "parse_mode": "HTML"
                    },
                    files={
                        "photo": (
                            "news.jpg",
                            image_response.content,
                            content_type
                        )
                    }
                )
                return

        except (requests.RequestException, RuntimeError) as exc:
            print(f"ارسال تصویر ناموفق بود: {exc}")

    # در صورت نبود تصویر یا خطای ارسال، متن خبر منتشر می‌شود
    telegram_request(
        "sendMessage",
        {
            "chat_id": CHANNEL_ID,
            "text": caption[:4096],
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }
    )


def main():
    seen = load_seen()

    response = requests.get(
        FEED_URL, headers=HEADERS, timeout=30
    )
    response.raise_for_status()

    feed = feedparser.parse(response.content)

    if not feed.entries:
        raise RuntimeError(
            "فید RSS خبری پیدا نشد؛ باید آدرس فید سایت بررسی شود."
        )

    new_entries = []

    for entry in feed.entries:
        url = entry.get("link", "").strip()

        if not url or url in seen:
            continue

        new_entries.append(entry)

    # انتشار از قدیمی‌ترین خبر جدید به جدیدترین خبر
    for entry in reversed(new_entries):
        url = entry.get("link", "").strip()

        try:
            title, lead, image_url = get_article_data(url, entry)

            if not title:
                print(f"عنوان خبر موجود نیست: {url}")
                continue

            publish_news(title, lead, image_url, url)

            seen.add(url)
            save_seen(seen)

            print(f"خبر منتشر شد: {title}")

            time.sleep(2)

        except Exception as exc:
            print(f"خطا در انتشار خبر {url}: {exc}")
            raise


if __name__ == "__main__":
    main()
