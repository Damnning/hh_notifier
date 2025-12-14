import asyncio
import logging
import aiohttp
import json
import os
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message

# --- КОНФИГУРАЦИЯ ---
API_TOKEN = 'ВАШ_ТОКЕН'  # <--- Вставьте токен
CHECK_INTERVAL = 5 * 60  # Проверка каждые 5 минут
DB_FILE = "seen_vacancies.json"
CONFIG_FILE = "bot_config.json"
ALLOWED_USERS = [123456789]  # <--- Вставьте ваш ID

# ID регионов (можно найти на api.hh.ru/areas)
AREA_RUSSIA = 113
AREA_VORONEZH = 26

# Ключевые слова
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

# Черный список слов (фильтр мусора)
EXCLUDED_WORDS = [
    'системный', 'system',
    'администратор', 'administrator', 'admin',
    'преподаватель', 'teacher', 'mentor', 'ментор',
    'support', 'поддержки',
    'manager', 'менеджер',
    'sales', 'продаж',
    '1с', '1c',
    'бизнес-аналитик', 'business analyst',
    'директор', 'head', 'cfo', 'ceo', 'lead'
]

HH_HEADERS = {"User-Agent": "MyTelegramBot/6.0 (myemail@example.com)"}

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher()
monitoring_task = None
seen_vacancies = set()
active_chat_id = None


# --- РАБОТА С ФАЙЛАМИ ---
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


# --- API ---
async def get_vacancies(query, area_id, schedule=None):
    """
    Универсальная функция поиска.
    :param query: текст запроса
    :param area_id: ID региона (113 Россия или 26 Воронеж)
    :param schedule: 'remote' для удаленки или None для всего остального
    """
    url = "https://api.hh.ru/vacancies"
    params = {
        "text": query,
        "area": area_id,
        "per_page": 20,
        "order_by": "publication_time",
        "search_field": "name"  # Ищем только в названии
    }
    # Если передали параметр расписания (например, remote), добавляем его
    if schedule:
        params["schedule"] = schedule

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


# --- ФИЛЬТР ---
def is_relevant(title):
    title_lower = title.lower()
    for bad_word in EXCLUDED_WORDS:
        if bad_word in title_lower:
            return False
    return True


# --- ЦИКЛ ПРОВЕРКИ ---
async def scheduled_checker():
    global seen_vacancies
    first_run = len(seen_vacancies) == 0

    if active_chat_id:
        await bot.send_message(active_chat_id, "🌍 Фильтр: Воронеж ИЛИ Удаленка (РФ).")

    while True:
        if not active_chat_id:
            await asyncio.sleep(5)
            continue

        try:
            found_new = False

            for query in SEARCH_QUERIES:
                # Делаем ДВА запроса для каждого слова

                # 1. Ищем удаленку по всей России
                remote_jobs = await get_vacancies(query, area_id=AREA_RUSSIA, schedule='remote')

                # 2. Ищем всё в Воронеже (и офис, и гибрид, и удаленку)
                voronezh_jobs = await get_vacancies(query, area_id=AREA_VORONEZH, schedule=None)

                # Объединяем списки
                all_items = remote_jobs + voronezh_jobs

                # Обрабатываем (используем reversed, чтобы сначала обрабатывать старые из пачки)
                # Важно: из-за объединения списков порядок может сбиться, но для уведомлений это не критично
                for vac in reversed(all_items):
                    v_id = vac['id']
                    v_title = vac['name']

                    if v_id not in seen_vacancies:
                        seen_vacancies.add(v_id)

                        # Фильтр стоп-слов
                        if not is_relevant(v_title):
                            continue

                        found_new = True

                        if not first_run:
                            # Достаем инфу о графике и городе для красоты
                            schedule_name = vac.get('schedule', {}).get('name', '')
                            area_name = vac.get('area', {}).get('name', '')

                            # Ставим эмодзи в зависимости от типа
                            loc_emoji = "🏠" if "удаленная" in schedule_name.lower() else "🏢"

                            text = (
                                f"🔥 <b>{query}</b>\n"
                                f"💼 {v_title}\n"
                                f"{loc_emoji} {area_name} • {schedule_name}\n"
                                f"🏦 {vac['employer']['name']}\n"
                                f"💰 {format_salary(vac['salary'])}\n"
                                f"🔗 <a href='{vac['alternate_url']}'>Откликнуться</a>"
                            )
                            try:
                                await bot.send_message(active_chat_id, text, parse_mode="HTML",
                                                       disable_web_page_preview=True)
                                await asyncio.sleep(1)
                            except Exception:
                                pass

                # Пауза между ключевыми словами
                await asyncio.sleep(2)

            if found_new:
                save_vacancies()

            if first_run:
                first_run = False
                await bot.send_message(active_chat_id, "✅ База обновлена. Мониторинг активен.")

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
        await message.answer("Мониторинг запущен.")
    else:
        await message.answer("Я работаю!")


async def main():
    global monitoring_task
    load_data()
    monitoring_task = asyncio.create_task(scheduled_checker())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

