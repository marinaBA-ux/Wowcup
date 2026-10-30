import asyncio
import json
import os
from pathlib import Path

import requests
from playwright.async_api import async_playwright

TARGET_URL = os.environ["TARGET_URL"]
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
STATE_FILE = Path("state.json")

UNAVAILABLE = [
    "билетов нет", "билеты закончились", "нет билетов",
    "распродано", "продажи завершены", "sold out", "no tickets",
]
BUY_MARKERS = [
    "купить билет", "купить билеты", "выбрать места",
    "перейти к покупке", "оформить заказ",
]


def telegram(message: str):
    response = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": message, "disable_web_page_preview": False},
        timeout=20,
    )
    response.raise_for_status()


async def check_page():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(8000)
            text = " ".join((await page.locator("body").inner_text(timeout=20000)).lower().split())
            has_buy = any(x in text for x in BUY_MARKERS)
            sold_out = any(x in text for x in UNAVAILABLE)
            return has_buy and not sold_out, text[:1500]
        finally:
            await browser.close()


async def main():
    available, excerpt = await check_page()
    state = {"available": False}
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    previously_available = bool(state.get("available"))
    STATE_FILE.write_text(
        json.dumps({"available": available, "excerpt": excerpt}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if available and not previously_available:
        telegram(
            "🎟️ Похоже, на Яндекс Афише появились билеты!\n"
            f"{TARGET_URL}\n\n"
            "Проверь страницу: автоматическое распознавание может ошибаться, "
            "если Яндекс изменит интерфейс."
        )


if __name__ == "__main__":
    asyncio.run(main())
