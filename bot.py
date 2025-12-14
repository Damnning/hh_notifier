import asyncio
import logging
import aiohttp
import json
import os
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message

# --- КОНФИГУРАЦИЯ ---
API_TOKEN = '8276972592:AAFnjZoprMGjmJAzqs7Pb8t3DhR64EMXesM'
CHECK_INTERVAL = 10 * 60
DB_FILE = "seen_vacancies.json"
CONFIG_FILE = "bot_config.json"  # <--- Файл для настроек (кто запустил бота)

# Ваш ID (белый список)
ALLOWED_USERS = [686621427]

# 1. Более точные запросы
# Используем кавычки для точных фраз, чтобы ML не путался с XML и т.д.
SEARCH_QUERIES = [
    'Python developer',
    'Data Scientist',
    'Data Engineer',
    'Machine Learning',
    'Computer Vision',
    'NLP',
    'R&D engineer',
    'Аналитик данных',
    'AI engineer'
]

# 2. Черный список слов в названии (в нижнем регистре)
# Если эти слова есть в заголовке - вакансия игнорируется
EXCLUDED_WORDS = [
    'системный', 'system',
    'администратор', 'administrator', 'admin',
    'преподаватель', 'teacher', 'курсов', 'куратор',
    'support', 'поддержки',
    'manager', 'менеджер',  # Чтобы убрать Affiliate Manager
    'sales', 'продаж',
    '1с', '1c',  # Часто лезет в аналитику
    'бизнес-аналитик', 'business analyst',
    'директор', 'head', 'cfo', 'ceo'
]

SEARCH_AREA = 113
HH_HEADERS = {"User-Agent": "MyTelegramBot/3.0 (danning600@gmail.com)"}

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher()
monitoring_task = None
seen_vacancies = set()
active_chat_id = None


# --- ФУНКЦИИ ---
def load_data():
    global seen_vacancies, active_chat_id
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                seen_vacancies = set(json.load(f))
        except Exception:
            seen_vacancies = set()

    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                active_chat_id = json.load(f).get("chat_id")
        except Exception:
            pass


def save_vacancies():
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(list(seen_vacancies), f)
    except Exception:
        pass


def save_config(chat_id):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"chat_id": chat_id}, f)
    except Exception:
        pass


async def get_vacancies(query):
    url = "https://api.hh.ru/vacancies"
    params = {
        "text": query,
        "area": SEARCH_AREA,
        "per_page": 20,  # Берем чуть больше, так как часть отфильтруем
        "order_by": "publication_time",

        # !!! ГЛАВНОЕ ИЗМЕНЕНИЕ !!!
        # Ищем только в названии вакансии.
        # Это уберет сисадминов, у которых Python просто упомянут в стеке.
        # "search_field": "name"
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, headers=HH_HEADERS) as response:
            if response.status == 200:
                data = await response.json()
                return data.get("items", [])
            return []


def format_salary(salary_data):
    if not salary_data: return "Не указана"
    s_from = salary_data.get('from')
    s_to = salary_data.get('to')
    cur = salary_data.get('currency', '')
    if s_from and s_to:
        return f"{s_from} - {s_to} {cur}"
    elif s_from:
        return f"от {s_from} {cur}"
    elif s_to:
        return f"до {s_to} {cur}"
    return "Не указана"


# --- ЛОГИКА ФИЛЬТРАЦИИ ---
def is_relevant(title):
    title_lower = title.lower()
    # Проверяем, нет ли запрещенных слов
    for bad_word in EXCLUDED_WORDS:
        if bad_word in title_lower:
            return False
    return True


async def scheduled_checker():
    global seen_vacancies
    first_run = len(seen_vacancies) == 0

    if active_chat_id:
        await bot.send_message(active_chat_id, "🚀 Умный фильтр вакансий запущен.")

    while True:
        if not active_chat_id:
            await asyncio.sleep(5)
            continue

        try:
            found_new = False
            for query in SEARCH_QUERIES:
                items = await get_vacancies(query)

                # Идем по списку полученных вакансий
                for vac in reversed(items):
                    v_id = vac['id']
                    v_title = vac['name']

                    if v_id not in seen_vacancies:
                        seen_vacancies.add(v_id)

                        # !!! ФИЛЬТРАЦИЯ !!!
                        # Если вакансия содержит стоп-слова, мы её помечаем как "просмотренную",
                        # но НЕ отправляем в чат.
                        if not is_relevant(v_title):
                            continue

                        found_new = True

                        if not first_run:
                            text = (
                                f"🔥 <b>{query}</b>\n"
                                f"💼 {v_title}\n"
                                f"🏢 {vac['employer']['name']}\n"
                                f"💰 {format_salary(vac['salary'])}\n"
                                f"🔗 <a href='{vac['alternate_url']}'>Ссылка</a>"
                            )
                            try:
                                await bot.send_message(active_chat_id, text, parse_mode="HTML",
                                                       disable_web_page_preview=True)
                                await asyncio.sleep(1)
                            except Exception:
                                pass

                await asyncio.sleep(2)

            if found_new:
                save_vacancies()

            if first_run:
                first_run = False
                await bot.send_message(active_chat_id, "✅ Первичный анализ завершен. Жду только релевантные.")

        except Exception as e:
            logging.error(f"Error: {e}")

        await asyncio.sleep(CHECK_INTERVAL)


@dp.message(Command("start"))
async def cmd_start(message: Message):
    global active_chat_id, monitoring_task
    if message.from_user.id not in ALLOWED_USERS: return
    active_chat_id = message.chat.id
    save_config(active_chat_id)
    if monitoring_task is None:
        await message.answer("Мониторинг обновлен.")
    else:
        await message.answer("Работаю!")


async def main():
    global monitoring_task
    load_data()
    monitoring_task = asyncio.create_task(scheduled_checker())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())