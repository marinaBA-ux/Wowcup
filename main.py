import asyncio
import json
import logging
import os
from pathlib import Path
from datetime import datetime, timezone

from playwright.async_api import async_playwright
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TARGET_URL = os.getenv(
    "TARGET_URL",
    "https://widget.afisha.yandex.ru/w/sessions/MTEwOTYzfDkwODE5MnwxNDk2OTE2MnwxNzk2NDc3NDAwMDAw?clientKey=5a2f3e1d-f923-4b55-8a34-636ba6dfe922",
)
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "600"))
STATE_FILE = Path(os.getenv("STATE_FILE", "state.json"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("afisha-ticket-bot")

chat_id = None
monitor_task = None


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            log.exception("Не удалось прочитать state.json")
    return {"available": False, "last_checked": None, "last_excerpt": ""}


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def inspect_page(browser):
    page = await browser.new_page()
    try:
        await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)
        # Даём виджету время загрузить динамическое содержимое.
        await page.wait_for_timeout(8000)
        body_text = await page.locator("body").inner_text(timeout=15000)
        normalized = " ".join(body_text.lower().split())

        unavailable_markers = [
            "билетов нет",
            "билеты закончились",
            "нет билетов",
            "распродано",
            "продажи завершены",
            "sold out",
            "no tickets",
        ]
        purchase_markers = [
            "купить билет",
            "купить билеты",
            "выбрать места",
            "перейти к покупке",
            "оформить заказ",
        ]

        unavailable = any(marker in normalized for marker in unavailable_markers)
        purchase_action = any(marker in normalized for marker in purchase_markers)

        # Консервативная проверка: считаем билеты доступными только при наличии
        # явного действия покупки и отсутствии явного сообщения о распродаже.
        available = purchase_action and not unavailable

        excerpt = body_text[:1200].strip()
        return available, excerpt
    finally:
        await page.close()


async def monitor(app: Application):
    global chat_id
    state = load_state()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            while True:
                if chat_id is not None:
                    try:
                        available, excerpt = await inspect_page(browser)
                        was_available = bool(state.get("available", False))
                        state["last_checked"] = datetime.now(timezone.utc).isoformat()
                        state["last_excerpt"] = excerpt
                        state["available"] = available
                        save_state(state)

                        log.info("Проверка завершена: available=%s", available)
                        if available and not was_available:
                            await app.bot.send_message(
                                chat_id=chat_id,
                                text=(
                                    "🎟️ Похоже, на Яндекс Афише появились билеты!\n"
                                    f"{TARGET_URL}\n\n"
                                    "Проверь страницу и наличие мест: автоматическая проверка "
                                    "может ошибаться, если Яндекс изменит интерфейс."
                                ),
                                disable_web_page_preview=False,
                            )
                    except Exception:
                        log.exception("Ошибка при проверке страницы")
                        try:
                            await app.bot.send_message(
                                chat_id=chat_id,
                                text="⚠️ Не удалось проверить Яндекс Афишу. Попробую ещё раз позже.",
                            )
                        except Exception:
                            log.exception("Не удалось отправить сообщение об ошибке")
                await asyncio.sleep(CHECK_INTERVAL_SECONDS)
        finally:
            await browser.close()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global chat_id, monitor_task
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        "Бот подключён! Буду проверять страницу Яндекс Афиши примерно каждые "
        f"{CHECK_INTERVAL_SECONDS // 60} минут.\n"
        "Команда /status покажет состояние мониторинга."
    )
    if monitor_task is None or monitor_task.done():
        monitor_task = asyncio.create_task(monitor(context.application))


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    last = state.get("last_checked") or "проверок пока не было"
    available = "похоже, доступны" if state.get("available") else "пока не обнаружены"
    await update.message.reply_text(
        f"Билеты: {available}.\nПоследняя проверка: {last}.\n"
        f"Интервал: {CHECK_INTERVAL_SECONDS // 60} минут."
    )


async def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    log.info("Бот запущен. Открой Telegram и отправь ему /start.")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    try:
        # Держим процесс активным; задача мониторинга создаётся после /start.
        while True:
            await asyncio.sleep(3600)
    finally:
        if monitor_task and not monitor_task.done():
            monitor_task.cancel()
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
