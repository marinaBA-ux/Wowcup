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

            # Нажимаем кнопку и перехватываем новую вкладку
            log("Шаг 3. Нажимаем кнопку «Купить билет»")

            try:
                async with page.expect_popup(timeout=20000) as popup_info:
                    await page.locator("button.header-ticket").first.click()

                ticket_page = await popup_info.value

            except Exception as e:
                raise Exception(
                    f"Не удалось открыть новую вкладку с билетами: {e}"
                ) from e

            await ticket_page.wait_for_load_state(
                "domcontentloaded",
                timeout=60000,
            )

            await ticket_page.wait_for_timeout(8000)

            log(f"Шаг 4. Адрес страницы покупки: {ticket_page.url}")

            # Проверяем, что открылась именно Яндекс Афиша
            from urllib.parse import urlparse

            hostname = urlparse(ticket_page.url).hostname or ""

            if hostname not in [
                "widget.afisha.yandex.ru",
                "afisha.yandex.ru",
            ] and not hostname.endswith(".afisha.yandex.ru"):
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

            # Проверяем, проданы ли билеты
            sold_out_markers = [
                "билеты проданы",
                "билетов нет",
                "билеты закончились",
                "распродано",
                "sold out",
                "no tickets",
            ]

            # Проверяем технические ошибки
            error_markers = [
                "код ошибки",
                "ошибка загрузки",
                "страница недоступна",
                "что-то пошло не так",
            ]

            if any(marker in text for marker in sold_out_markers):
                log("Результат: сообщение о продаже билетов присутствует")
                return False, ticket_page.url, text[:1500]

            if any(marker in text for marker in error_markers):
                raise Exception(
                    "Обнаружена техническая ошибка. "
                    "Статус билетов определить невозможно."
                )

            # Сообщение о проданных билетах исчезло
            log("ВНИМАНИЕ: сообщение «Билеты проданы» исчезло!")
            log("Статус страницы изменился. Требуется ручная проверка.")

            return True, ticket_page.url, text[:1500]

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
        log("Изменение статуса! Отправляем уведомление")
        telegram(
            "🎾 WOW CUP — ИЗМЕНЕНИЕ СТАТУСА!\n\n"
            "Сообщение «Билеты проданы» исчезло.\n"
            "Возможно, началась продажа билетов!\n\n"
            f"Проверить билеты:\n{ticket_url}\n\n"
            "Открой страницу и проверь наличие свободных мест."
        )
        log("Уведомление отправлено в Telegram")
    else:
        log("Новых изменений по результатам проверки не обнаружено")

    log("Проверка завершена успешно")


if __name__ == "__main__":
    asyncio.run(main())
