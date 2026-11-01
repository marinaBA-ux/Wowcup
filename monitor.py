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


def log(message):
    print(f"[BOT] {message}", flush=True)


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
            log("Шаг 1. Открываем главную страницу WOW Cup")

            await page.goto(
                HOME_URL,
                wait_until="domcontentloaded",
                timeout=60000,
            )
            await page.wait_for_timeout(5000)

            log(f"Главная страница открыта: {page.url}")

            log("Шаг 2. Ищем кнопку «Купить билет»")

            button = page.get_by_text(
                re.compile("купить билет", re.IGNORECASE)
            ).first

            if await button.count() == 0:
                raise Exception("Кнопка покупки не найдена")

            log("Кнопка найдена")

            # Диагностика структуры кнопки
            button_info = await button.evaluate("""
                el => {
                    const result = [];
                    let node = el;

                    for (let i = 0; i < 5 && node; i++) {
                        result.push({
                            tag: node.tagName,
                            html: node.outerHTML.substring(0, 1000),
                            href: node.href || null,
                            onclick: node.getAttribute('onclick'),
                            dataHref: node.getAttribute('data-href'),
                            dataUrl: node.getAttribute('data-url')
                        });
                        node = node.parentElement;
                    }

                    return result;
                }
            """)

            log(f"Структура кнопки: {json.dumps(button_info, ensure_ascii=False)}")

            raise Exception("Диагностика завершена. Адрес кнопки пока не определён.")

            # Открываем именно ссылку покупки в отдельной вкладке
            ticket_page = await page.context.new_page()

            await ticket_page.goto(
                ticket_url,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            await ticket_page.wait_for_timeout(8000)

            log(f"Шаг 4. Адрес страницы покупки: {ticket_page.url}")

            # Проверяем, что мы действительно на Яндекс Афише
            from urllib.parse import urlparse

            hostname = urlparse(ticket_page.url).hostname or ""

            if not (
                hostname == "afisha.yandex.ru"
                or hostname.endswith(".afisha.yandex.ru")
            ):
                raise Exception(
                    f"Открыта не страница Яндекс Афиши: {ticket_page.url}"
                )

            text = " ".join(
                (await ticket_page.locator("body").inner_text(timeout=20000))
                .lower()
                .split()
            )

            if not text:
                raise Exception("Страница покупки не содержит текста")

            log("Шаг 5. Текст страницы получен")
            log(f"Содержимое страницы: {text[:1500]}")

            # Проверяем отсутствие билетов
            sold_out_markers = [
                "билеты проданы",
                "билетов нет",
                "билеты закончились",
                "распродано",
                "sold out",
                "no tickets",
            ]

            if any(marker in text for marker in sold_out_markers):
                log("Результат: билеты пока проданы")
                return False, ticket_page.url, text[:1500]

            # Проверяем технические ошибки, если статус билетов не определён
            error_markers = [
                "код ошибки",
                "ошибка загрузки",
                "страница недоступна",
                "что-то пошло не так",
            ]

            if any(marker in text for marker in error_markers):
                raise Exception(
                    "Яндекс Афиша вернула техническую ошибку. "
                    "Статус билетов определить невозможно."
                )

            # Проверяем явные признаки доступности билетов
            available_markers = [
                "выбрать места",
                "выбрать билет",
                "выбрать билеты",
                "перейти к покупке",
                "оформить заказ",
            ]

            available = any(
                marker in text for marker in available_markers
            )

            if available:
                log("Результат: обнаружены признаки доступных билетов")
            else:
                log(
                    "Результат: статус не определён. "
                    "Уведомление отправлять нельзя."
                )

            return available, ticket_page.url, text[:1500]

        finally:
            await browser.close()


async def main():
    log("Начинаем проверку WOW Cup")

    available, ticket_url, excerpt = await check_page()


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

    log("Шаг 6. Состояние сохранено")

    if available:
        log("Билеты доступны! Отправляем уведомление")

        telegram(
            "🎟️ Возможно, на WOW Cup появились билеты!\n\n"
            f"Страница покупки: {ticket_url}\n\n"
            f"Текст страницы:\n{excerpt}\n\n"
            "Проверь наличие билетов вручную."
        )

        log("Уведомление отправлено в Telegram")
    else:
        log("Новых билетов по результатам проверки не обнаружено")

    log("Проверка завершена успешно")


if __name__ == "__main__":
    asyncio.run(main())
