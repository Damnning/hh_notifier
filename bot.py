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
CONFIG_FILE = "bot_config.json"
ALLOWED_USERS = [686621427]

# ID регионов (можно найти на api.hh.ru/areas)
AREA_RUSSIA = 113
AREA_VORONEZH = 26

# Оставляем только "Без опыта" и "1-3 года"
TARGET_EXPERIENCE = ["noExperience", "between1And3"]

# Ключевые слова
SEARCH_QUERIES = [
    # 1. Data Scientist / ML Engineer (Твой основной профиль)
    'Data Scientist',
    'ML Engineer',
    'Machine Learning',
    'Computer Vision',
    'NLP',
    'AI engineer',

    # 2. Python Backend (У тебя сильный стек: FastAPI, Docker, AsyncIO)
    'Python developer',
    'Python backend',
    'Разработчик Python',

    # 3. Data Analyst (Твой опыт с SQL, EDA, Pandas)
    'Data Analyst',
    'Аналитик данных',
    'Product Analyst',
    'ETL developer'
    
    'R&D engineer',

]

# Черный список слов (фильтр мусора)
EXCLUDED_WORDS = [
    'Fullstack', 'Senior', 'Ведущий', 'React', 'Vue', 'Lead',
    'администратор', 'administrator', 'admin',
    'support', 'поддержки',
    'manager', 'менеджер',  # Чтобы убрать Affiliate Manager
    'sales', 'продаж',
    '1с', '1c',  # Часто лезет в аналитику
    'директор', 'head', 'cfo', 'ceo'

    # Уровни, которые точно рано
                               'Principal', 'Руководитель', 'Начальник', 'CTO', 'Team Lead', 'Архитектор', 'Middle'

    # Не твой стек (ты Python)
                                                                                                           'Java', 'C#',
    '.NET', 'C++', 'PHP', 'Go', 'Golang', 'Ruby', '1C', '1С',
    'Bitrix', 'Битрикс', 'Wordpress',

    # Frontend (ты Backend/ML)
    'Frontend', 'React', 'Vue', 'Angular', 'JS', 'TypeScript', 'Node.js', 'Fullstack', 'Верстальщик',

    # Другие роли
    'QA', 'Tester', 'Тестировщик', 'Support', 'Поддержка', 'Администратор', 'Administrator', 'Sysadmin', 'DevOps',
    # DevOps часто требуют совсем другой стек, хотя Docker ты знаешь
    'Manager', 'Менеджер', 'Sales', 'Продажи', 'Mentor', 'Tutor', 'Куратор',
    'Business Analyst', 'Бизнес-аналитик'  # Это про процессы, а не про код/данные
]

HH_HEADERS = {"User-Agent": "MyTelegramBot/3.0 (danning600@gmail.com)"}

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
    Универсальная функция поиска с фильтром опыта.
    """
    url = "https://api.hh.ru/vacancies"
    params = {
        "text": query,
        "area": area_id,
        "per_page": 20,
        "order_by": "publication_time",
        "search_field": "name",
        "experience": TARGET_EXPERIENCE  # <--- ДОБАВИЛИ ФИЛЬТР ПО ОПЫТУ
    }

    if schedule:
        params["schedule"] = schedule

    async with aiohttp.ClientSession() as session:
        # aiohttp автоматически превратит список experience в experience=...&experience=...
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
        await bot.send_message(active_chat_id, "🌍 Фильтр: (Воронеж ИЛИ Удаленка) + (Без опыта ИЛИ 1-3 года).")

    while True:
        if not active_chat_id:
            await asyncio.sleep(5)
            continue

        try:
            found_new = False

            for query in SEARCH_QUERIES:
                # 1. Удаленка (РФ) + нужный опыт
                remote_jobs = await get_vacancies(query, area_id=AREA_RUSSIA, schedule='remote')

                # 2. Воронеж (любой график) + нужный опыт
                voronezh_jobs = await get_vacancies(query, area_id=AREA_VORONEZH, schedule=None)

                all_items = remote_jobs + voronezh_jobs

                for vac in reversed(all_items):
                    v_id = vac['id']
                    v_title = vac['name']

                    if v_id not in seen_vacancies:
                        seen_vacancies.add(v_id)

                        if not is_relevant(v_title):
                            continue

                        found_new = True

                        if not first_run:
                            schedule_name = vac.get('schedule', {}).get('name', '')
                            area_name = vac.get('area', {}).get('name', '')
                            exp_name = vac.get('experience', {}).get('name', '')  # Получаем название опыта для вывода

                            loc_emoji = "🏠" if "удаленная" in schedule_name.lower() else "🏢"

                            text = (
                                f"🔥 <b>{query}</b>\n"
                                f"💼 {v_title}\n"
                                f"🎓 Опыт: {exp_name}\n"  # Добавил строку про опыт в сообщение
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
