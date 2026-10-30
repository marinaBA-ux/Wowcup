import asyncio
import json
import os
import re
from pathlib import Path

import requests
from playwright.async_api import async_playwright

HOME_URL = "https://wowcup.tennis/"
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

STATE_FILE = Path("state.json")

SOLD_OUT_TEXT = "билеты проданы"


def telegram(message: str):
    response = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={
            "chat_id": CHAT_ID,
            "text": message,
            "disable_web_page_preview": False,
        },
        timeout=20,
    )
    response.raise_for_status()


async def check_page():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        try:
            # 1. Открываем главную страницу WOW Cup
            await page.goto(
                HOME_URL,
                wait_until="domcontentloaded",
                timeout=60000,
            )
            await page.wait_for_timeout(5000)

            # 2. Ищем кнопку «Купить билет»
            button = page.get_by_text(
                re.compile("купить билет", re.IGNORECASE)
            ).first

            if await button.count() == 0:
                raise Exception("На главной странице не найдена кнопка «Купить билет»")

            # 3. Нажимаем кнопку и отслеживаем открытие новой вкладки
            popups = []
            page.on("popup", lambda popup: popups.append(popup))

            await button.click(timeout=15000)
            await page.wait_for_timeout(7000)

            # Если открылась новая вкладка — проверяем её.
            # Если переход произошёл в этой же вкладке — проверяем её.
            ticket_page = popups[0] if popups else page

            await ticket_page.wait_for_load_state(
                "domcontentloaded",
                timeout=30000,
            )
            await ticket_page.wait_for_timeout(5000)

            # 4. Проверяем содержимое страницы покупки
            text = " ".join(
                (await ticket_page.locator("body").inner_text(timeout=20000))
                .lower()
                .split()
            )

            if not text:
                raise Exception("Страница покупки открылась, но текст не найден")

            available = SOLD_OUT_TEXT not in text

            return available, ticket_page.url, text[:1500]

        finally:
            await browser.close()


async def main():
    available, ticket_url, excerpt = await check_page()

    # Читаем предыдущее состояние
    state = {"available": False}

    if STATE_FILE.exists():
        try:
            state = json.loads(
                STATE_FILE.read_text(encoding="utf-8")
            )
        except Exception:
            pass

    previously_available = bool(state.get("available"))

    # Сохраняем новое состояние
    STATE_FILE.write_text(
        json.dumps(
            {
                "available": available,
                "url": ticket_url,
                "excerpt": excerpt,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # Отправляем сообщение только при изменении статуса
    if available and not previously_available:
        telegram(
            "🎟️ Возможно, на WOW Cup появились билеты!\n\n"
            f"Страница покупки: {ticket_url}\n\n"
            f"Текст страницы:\n{excerpt}\n\n"
            "Проверь наличие билетов вручную."
        )


if __name__ == "__main__":
    asyncio.run(main())
