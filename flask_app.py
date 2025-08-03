# flask_app.py
import os
import re
import zipfile
import traceback
import logging
import time
import json
import random
import threading
from io import BytesIO

import requests
import pandas as pd
from flask import Flask, request
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text, MetaData, Table, Column, Integer, String, Text
from sqlalchemy.exc import OperationalError

# --- Настройка логирования ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# --- Конфигурация ---
DB_PATH = '/home/AnatolySamoylenko/tittle_database.db'
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DB_PATH}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ECHO'] = False

db = SQLAlchemy(app)

# --- Кэширование ---
_tables_exist_cache = {}
_user_shop_cache = {}

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
if not TELEGRAM_TOKEN:
    logger.warning("Переменная окружения TELEGRAM_TOKEN не установлена!")

# --- Список популярных User-Agent'ов ---
POPULAR_USER_AGENTS = [
    # Chrome on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36",
    # Safari on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Safari/605.1.15",
    # Chrome on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36",
    # Chrome on Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
    # Edge on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36 Edg/117.0.2045.47"
]

# --- Модели БД ---
# Базовая модель пользователя - общая для всех
class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    chat_id = db.Column(db.String(50), unique=True, nullable=False)
    username = db.Column(db.String(100))

# --- Вспомогательные функции для работы с персональными таблицами ---
def get_personal_shop_table_name(chat_id):
    """Генерирует имя персональной таблицы shops для пользователя."""
    return f'shops_{chat_id}'

def get_personal_phrase_table_name(chat_id):
    """Генерирует имя персональной таблицы phrases для пользователя."""
    return f'phrases_{chat_id}'

def create_personal_shop_table(chat_id):
    """Создает персональную таблицу shops для пользователя, если она не существует."""
    table_name = get_personal_shop_table_name(chat_id)
    with app.app_context():
        inspector = inspect(db.engine)
        if table_name not in inspector.get_table_names():
            logger.info(f"Создаем персональную таблицу shops: {table_name}")
            metadata = MetaData()
            table = Table(table_name, metadata,
                          Column('shopId', Integer, primary_key=True, autoincrement=False),
                          Column('API', Text, nullable=False),
                          Column('chatId', Integer, nullable=False),
            )
            metadata.create_all(db.engine)
            logger.info(f"Таблица {table_name} создана.")
        else:
            logger.debug(f"Персональная таблица shops {table_name} уже существует.")

def create_personal_phrase_table(chat_id):
    """Создает персональную таблицу phrases для пользователя, если она не существует."""
    table_name = get_personal_phrase_table_name(chat_id)
    with app.app_context():
        inspector = inspect(db.engine)
        if table_name not in inspector.get_table_names():
            logger.info(f"Создаем персональную таблицу phrases: {table_name}")
            metadata = MetaData()
            table = Table(table_name, metadata,
                          Column('phrase', Text, primary_key=True, nullable=False),
                          Column('qntyPerDay', Integer, nullable=False),
                          Column('subject', Text, nullable=False),
                          Column('preset', Integer, nullable=False),
                          Column('normQuery', Text, nullable=True),
                          Column('auto', Integer, nullable=False),
                          Column('auction', Integer, nullable=False),
                          Column('total', Integer, nullable=False),
            )
            metadata.create_all(db.engine)
            logger.info(f"Таблица {table_name} создана.")
        else:
            logger.debug(f"Персональная таблица phrases {table_name} уже существует.")

# --- Функции для работы с данными в персональных таблицах ---
def get_personal_shop_model(chat_id):
    """Динамически создает модель SQLAlchemy для персональной таблицы shops пользователя."""
    table_name = get_personal_shop_table_name(chat_id)
    create_personal_shop_table(chat_id)
    
    class PersonalShop(db.Model):
        __tablename__ = table_name
        __table_args__ = {'extend_existing': True}
        shopId = db.Column(db.Integer, primary_key=True, autoincrement=False)
        API = db.Column(db.Text, nullable=False)
        chatId = db.Column(db.Integer, nullable=False)
        
    return PersonalShop

def get_personal_phrase_model(chat_id):
    """Динамически создает модель SQLAlchemy для персональной таблицы phrases пользователя."""
    table_name = get_personal_phrase_table_name(chat_id)
    create_personal_phrase_table(chat_id)
    
    class PersonalPhrase(db.Model):
        __tablename__ = table_name
        __table_args__ = {'extend_existing': True}
        phrase = db.Column(db.Text, primary_key=True, nullable=False)
        qntyPerDay = db.Column(db.Integer, nullable=False)
        subject = db.Column(db.Text, nullable=False)
        preset = db.Column(db.Integer, nullable=False)
        normQuery = db.Column(db.Text, nullable=True)
        auto = db.Column(db.Integer, nullable=False)
        auction = db.Column(db.Integer, nullable=False)
        total = db.Column(db.Integer, nullable=False)
        
        def __repr__(self):
            return f"<PersonalPhrase(phrase='{self.phrase}', qntyPerDay={self.qntyPerDay}, subject='{self.subject}')>"
            
    return PersonalPhrase

def _check_tables_exist():
    """Проверяет существование основных таблиц (users), используя кэш."""
    global _tables_exist_cache
    cache_key = "main_tables_exist"
    if cache_key in _tables_exist_cache:
        return _tables_exist_cache[cache_key]

    try:
        inspector = inspect(db.engine)
        existing_tables = set(inspector.get_table_names())
        required_tables = {'users'}
        
        result = required_tables.issubset(existing_tables)
        _tables_exist_cache[cache_key] = result
        if not result:
            missing = required_tables - existing_tables
            logger.info(f"Отсутствующие основные таблицы: {missing}")
        return result
    except Exception as e:
        logger.error(f"Ошибка при проверке основных таблиц: {e}")
        return False

def initialize_database_if_needed():
    """Ленивая инициализация основных таблиц БД при первом обращении."""
    if not _check_tables_exist():
        logger.info("Основные таблицы отсутствуют, инициализируем БД...")
        with app.app_context():
            try:
                db.create_all()
                _tables_exist_cache.clear()
                logger.info("Основные таблицы успешно созданы.")
            except Exception as e:
                logger.error(f"Ошибка при создании основных таблиц: {e}")
                raise

def send_message(chat_id, text):
    """Отправляет текстовое сообщение в Telegram."""
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN не установлен!")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
        logger.debug(f"Сообщение отправлено пользователю {chat_id}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка отправки сообщения пользователю {chat_id}: {e}")

def extract_dates_from_filename_simple(filename):
    """Простое извлечение дат для логирования."""
    pattern = r'[сcCc][\s_\-\.]*([\d\-\./]+)[\s_\-\.]*[пnN][оoO][\s_\-\.]*([\d\-\./]+)'
    match = re.search(pattern, filename)
    if match:
        return match.group(1), match.group(2)
    return None, None

# --- ОБНОВЛЕННАЯ ФУНКЦИЯ: Построчная обработка XLSX с персональными таблицами ---
# НАЧИНАЕТ обработку С САМОЙ ПЕРВОЙ СТРОКИ ДАННЫХ (без пропуска первых 2 строк)
def process_phrases_from_xlsx(df, chat_id):
    """
    Обрабатывает DataFrame с данными фраз и сохраняет их в ПЕРСОНАЛЬНУЮ БД ПОСТРОЧНО.
    Использует колонки: 0 (Поисковый запрос), 3 (Запросов в среднем за день), 5 (Больше всего заказов в предмете).
    НАЧИНАЕТ обработку С САМОЙ ПЕРВОЙ СТРОКИ ДАННЫХ (без пропуска первых 2 строк).
    """
    logger.info(f"Начинаем ПОСТРОЧНУЮ обработку DataFrame для пользователя {chat_id}. Форма: {df.shape}")

    PhraseModel = get_personal_phrase_model(chat_id)
    
    required_indices = {'phrase_col_idx': 0, 'qnty_col_idx': 3, 'subject_col_idx': 5}
    max_required_idx = max(required_indices.values())

    if max_required_idx >= len(df.columns):
        raise ValueError(
            f"Файл не содержит достаточно колонок. "
            f"Требуется как минимум {max_required_idx + 1} колонок. Найдено {len(df.columns)}."
        )

    try:
        data_slice = df.iloc[:, [required_indices['phrase_col_idx'],
                                 required_indices['qnty_col_idx'],
                                 required_indices['subject_col_idx']]].copy()
        data_slice.columns = ['phrase_raw', 'qntyPerDay_raw', 'subject_raw']
    except Exception as e:
        logger.error(f"Ошибка при выборе колонок: {e}")
        raise ValueError(f"Ошибка при выборе колонок: {e}")

    total_rows = len(data_slice)
    logger.info(f"Будет обработано {total_rows} строк для пользователя {chat_id}.")

    if total_rows == 0:
        return 0, 0, 0

    phrases_added = 0
    phrases_updated = 0
    processed_count = 0

    with app.app_context():
        try:
            # --- ИЗМЕНЕНО: Итерируемся по ВСЕМ строкам data_slice, начиная с индекса 0 ---
            # В предыдущей версии было: for index, row in data_slice.iloc[2:, :].iterrows():
            # Теперь: for index, row in data_slice.iterrows():
            for index, row in data_slice.iterrows():
                processed_count += 1
                try:
                    phrase_raw = row['phrase_raw']
                    phrase = str(phrase_raw).strip() if pd.notna(phrase_raw) else ""
                    
                    if not phrase:
                        logger.debug(f"[{processed_count}/{total_rows}] Пропущена пустая фраза в строке {index} для пользователя {chat_id}")
                        continue

                    qntyPerDay_raw = row['qntyPerDay_raw']
                    qntyPerDay = int(float(qntyPerDay_raw)) if pd.notna(qntyPerDay_raw) else 0
                    
                    subject_raw = row['subject_raw']
                    subject = str(subject_raw).strip() if pd.notna(subject_raw) else ""

                    existing_phrase = db.session.get(PhraseModel, phrase)
                    is_new = existing_phrase is None

                    if is_new:
                        phrase_obj = PhraseModel(
                            phrase=phrase,
                            qntyPerDay=qntyPerDay,
                            subject=subject,
                            preset=0,
                            normQuery=None,
                            auto=0,
                            auction=0,
                            total=0
                        )
                        db.session.add(phrase_obj)
                        phrases_added += 1
                        logger.debug(f"[{processed_count}/{total_rows}] Добавлена новая фраза: '{phrase}' для пользователя {chat_id}")
                    else:
                        db.session.delete(existing_phrase)
                        new_phrase_obj = PhraseModel(
                            phrase=phrase,
                            qntyPerDay=qntyPerDay,
                            subject=subject,
                            preset=0,
                            normQuery=None,
                            auto=0,
                            auction=0,
                            total=0
                        )
                        db.session.add(new_phrase_obj)
                        phrases_updated += 1
                        logger.debug(f"[{processed_count}/{total_rows}] Обновлена фраза: '{phrase}' для пользователя {chat_id}")

                    db.session.commit()
                    
                    # --- ИЗМЕНЕНО: Отправка уведомления о новой фразе отключена ---
                    # if is_new:
                    #      message_text = f"Новая фраза:\nФраза: {phrase}\nЗапросов в день: {qntyPerDay}\nПредмет: {subject}"
                    #      send_message(chat_id, message_text)

                except (ValueError, TypeError) as row_e:
                    logger.error(f"[{processed_count}/{total_rows}] Ошибка обработки строки {index} для пользователя {chat_id}: {row_e}")
                    db.session.rollback()
                    continue
                except Exception as row_e:
                    logger.error(f"[{processed_count}/{total_rows}] Неожиданная ошибка в строке {index} для пользователя {chat_id}: {row_e}")
                    db.session.rollback()
                    continue

                if processed_count % 100 == 0 or processed_count == total_rows:
                     progress_msg = f"Обработано {processed_count} из {total_rows} строк. Добавлено: {phrases_added}, Обновлено: {phrases_updated}"
                     logger.info(f"{progress_msg} для пользователя {chat_id}")
                     if processed_count % 1000 == 0 or processed_count == total_rows:
                         send_message(chat_id, progress_msg)

        except Exception as e:
            logger.error(f"Критическая ошибка в процессе обработки для пользователя {chat_id}: {e}")
            db.session.rollback()
            final_msg = f"Задача прервана. Обработано {processed_count} строк. Добавлено: {phrases_added}, Обновлено: {phrases_updated}. Ошибка: {e}"
            send_message(chat_id, final_msg)
            raise 

    logger.info(f"Построчная обработка завершена для пользователя {chat_id}. Всего: {total_rows}, Добавлено: {phrases_added}, Обновлено: {phrases_updated}")
    return phrases_added, phrases_updated, processed_count


def process_zip_and_xlsx(zip_content, original_filename, chat_id):
    """Распаковывает ZIP, находит XLSX, читает данные и сохраняет фразы в ПЕРСОНАЛЬНУЮ таблицу."""
    try:
        dates_start, dates_end = extract_dates_from_filename_simple(original_filename)
        logger.info(f"Даты из имени файла (для инфо): {dates_start} - {dates_end}")

        with zipfile.ZipFile(BytesIO(zip_content)) as zip_ref:
            xlsx_filename = next((f for f in zip_ref.namelist()
                                  if "аналитика поиска" in f.lower() and f.lower().endswith('.xlsx')), None)
            if not xlsx_filename:
                xlsx_filename = next((f for f in zip_ref.namelist() if f.lower().endswith('.xlsx')), None)
                if xlsx_filename:
                    logger.info(f"XLSX найден по расширению: {xlsx_filename}")

            if not xlsx_filename:
                return "Ошибка: В ZIP архиве не найден файл .xlsx."

            with zip_ref.open(xlsx_filename) as xlsx_file:
                excel_file = pd.ExcelFile(xlsx_file)
                target_sheet = next((name for name in excel_file.sheet_names
                                     if "детальная информация" in name.lower()), None)
                if not target_sheet and len(excel_file.sheet_names) > 2:
                    target_sheet = excel_file.sheet_names[2]
                    logger.info(f"Лист 'Детальная информация' не найден, используем: {target_sheet}")

                if not target_sheet:
                    return f"Ошибка: Не найден лист с данными. Доступны: {excel_file.sheet_names}"

                xlsx_file.seek(0)
                # Читаем с 4-й строки как заголовки (header=3)
                df = pd.read_excel(xlsx_file, sheet_name=target_sheet, header=3)

            if df.empty:
                return "Ошибка: Лист с данными пуст."

            added, updated, total = process_phrases_from_xlsx(df, chat_id)
            return f"Импорт завершен для пользователя {chat_id}.\nДобавлено: {added}\nОбновлено: {updated}\nВсего обработано: {total}"

    except zipfile.BadZipFile:
        return "Ошибка: Файл не является корректным ZIP архивом."
    except ValueError as e:
        return f"Ошибка данных: {e}"
    except Exception as e:
        logger.error(f"Неожиданная ошибка: {e}\n{traceback.format_exc()}")
        return f"Ошибка: {str(e)}"

# --- НОВАЯ ФУНКЦИЯ: Логика команды /searchads с персональными таблицами ---
def search_ads_task(chat_id):
    """
    Фоновая задача для выполнения поисковых запросов и обновления данных в ПЕРСОНАЛЬНОЙ БД.
    """
    logger.info(f"Запуск задачи search_ads для пользователя chat_id: {chat_id}")
    send_message(chat_id, f"Начинаю выполнение /searchads. Это может занять некоторое время...")

    try:
        PhraseModel = get_personal_phrase_model(chat_id)
        
        with app.app_context():
            phrases = db.session.query(PhraseModel).all()
            logger.info(f"Найдено {len(phrases)} фраз для обработки у пользователя {chat_id}.")

            if not phrases:
                send_message(chat_id, "В вашей персональной таблице phrases нет данных для обработки.")
                return

            # --- ИЗМЕНЕНО: Обновлен URL и параметры запроса ---
            base_url = "https://search.wb.ru/exactmatch/ru/common/v14/search"
            params_template = {
                "ab_testing": "false",
                "appType": "32",
                "curr": "rub",
                "dest": "-1257484",
                "lang": "ru",
                "page": "1", # Изменено с 8 на 1
                "resultset": "catalog",
                "sort": "popular",
                "spp": "99", # Изменено с 30 на 99
                "suppressSpellcheck": "false",
                "uclusters": "0", # Новый параметр
                "uiv": "0",       # Новый параметр
                "uv": "AQIDAAoEAAMlugAAKSg6si5xAVAO" # Новый параметр
            }
            # --- КОНЕЦ ИЗМЕНЕНИЙ ---

            updated_count = 0
            errors = []

            for i, phrase_obj in enumerate(phrases):
                phrase_text = phrase_obj.phrase
                logger.debug(f"[{i+1}/{len(phrases)}] Обрабатываем фразу: '{phrase_text}' для пользователя {chat_id}")

                if (i + 1) % 100 == 0:
                    send_message(chat_id, f"Обработано {i+1} из {len(phrases)} фраз...")

                params = params_template.copy()
                params["query"] = phrase_text

                max_total_retries = 5
                max_preset_retries = 3
                total_retries = 0
                preset_retries = 0
                success = False
                response_data = None

                while not success and (total_retries < max_total_retries or preset_retries < max_preset_retries):
                    try:
                        delay = random.uniform(3, 7)
                        logger.debug(f"Пауза {delay:.2f} секунд перед запросом для '{phrase_text}'...")
                        time.sleep(delay)

                        # --- ИЗМЕНЕНО: Добавлен случайный User-Agent ---
                        headers = {
                            'User-Agent': random.choice(POPULAR_USER_AGENTS)
                        }
                        # --- КОНЕЦ ИЗМЕНЕНИЙ ---

                        response = requests.get(base_url, params=params, timeout=15, headers=headers) # Добавлен headers
                        response.raise_for_status()
                        response_data = response.json()

                        metadata = response_data.get("metadata", {})
                        total = response_data.get("total", 0)
                        preset = metadata.get("presetId", 0)

                        logger.debug(f"Ответ для '{phrase_text}' пользователя {chat_id}: total={total}, preset={preset}")

                        if total == 0 and total_retries < max_total_retries:
                            total_retries += 1
                            logger.info(f"Total=0 для '{phrase_text}' пользователя {chat_id}, повтор ({total_retries}/{max_total_retries})...")
                            continue

                        if preset == 0 and preset_retries < max_preset_retries:
                            preset_retries += 1
                            logger.info(f"Preset=0 для '{phrase_text}' пользователя {chat_id}, повтор ({preset_retries}/{max_preset_retries})...")
                            continue

                        success = True

                    except requests.exceptions.RequestException as e:
                        logger.error(f"Ошибка запроса для фразы '{phrase_text}' пользователя {chat_id}: {e}")
                        errors.append(f"Фраза '{phrase_text}': Ошибка запроса - {e}")
                        break
                    except json.JSONDecodeError as e:
                        logger.error(f"Ошибка парсинга JSON для фразы '{phrase_text}' пользователя {chat_id}: {e}")
                        errors.append(f"Фраза '{phrase_text}': Ошибка парсинга JSON - {e}")
                        break
                    except Exception as e:
                        logger.error(f"Неожиданная ошибка для фразы '{phrase_text}' пользователя {chat_id}: {e}")
                        errors.append(f"Фраза '{phrase_text}': Неожиданная ошибка - {e}")
                        break

                if not success:
                    logger.warning(f"Не удалось получить корректные данные для фразы '{phrase_text}' пользователя {chat_id} после повторов.")
                    errors.append(f"Фраза '{phrase_text}': Не удалось получить данные после повторов.")
                    continue

                try:
                    tpc = 0
                    tpb = 0
                    products = response_data.get("products", [])
                    for product in products:
                        log = product.get("log", {})
                        tp = log.get("tp")
                        if tp == "c":
                            tpc += 1
                        elif tp == "b":
                            tpb += 1

                    metadata = response_data.get("metadata", {})
                    total = response_data.get("total", 0)
                    norm_query = metadata.get("normquery")
                    preset_id = metadata.get("presetId", 0)

                    logger.debug(f"Результаты для '{phrase_text}' пользователя {chat_id}: tpc={tpc}, tpb={tpb}, total={total}, normQuery='{norm_query}', preset={preset_id}")

                    phrase_obj.auction = tpc
                    phrase_obj.auto = tpb
                    phrase_obj.total = total
                    phrase_obj.normQuery = norm_query
                    phrase_obj.preset = preset_id

                    db.session.commit()
                    updated_count += 1

                    message = (
                        f"✅ Обновлена фраза: {phrase_text}\n"
                        f"   Auction (tp='c'): {tpc}\n"
                        f"   Auto (tp='b'): {tpb}\n"
                        f"   Total: {total}\n"
                        f"   Norm Query: {norm_query}\n"
                        f"   Preset: {preset_id}"
                    )
                    send_message(chat_id, message)

                except Exception as e:
                    logger.error(f"Ошибка обработки/обновления данных для фразы '{phrase_text}' пользователя {chat_id}: {e}")
                    errors.append(f"Фраза '{phrase_text}': Ошибка обработки данных - {e}")
                    db.session.rollback()

            final_message_lines = [f"✅ Задача /searchads завершена для пользователя {chat_id}."]
            final_message_lines.append(f"   Обработано фраз: {len(phrases)}")
            final_message_lines.append(f"   Успешно обновлено: {updated_count}")

            if errors:
                final_message_lines.append(f"   Ошибок: {len(errors)}")
                for err in errors[:5]:
                    final_message_lines.append(f"   - {err}")
                if len(errors) > 5:
                    final_message_lines.append(f"   ... и еще {len(errors) - 5} ошибок.")

            send_message(chat_id, "\n".join(final_message_lines))

    except Exception as e:
        logger.error(f"Критическая ошибка в задаче search_ads для пользователя {chat_id}: {e}")
        send_message(chat_id, f"❌ Критическая ошибка в задаче /searchads: {e}")

# --- НОВАЯ ФУНКЦИЯ: Логика команды /clearwords ---
def clear_phrases_task(chat_id):
    """
    Фоновая задача для очистки персональной таблицы phrases пользователя.
    """
    logger.info(f"Запуск задачи clear_phrases для пользователя chat_id: {chat_id}")
    send_message(chat_id, "Начинаю очистку таблицы phrases...")

    try:
        # Получаем персональную модель для этого пользователя
        PhraseModel = get_personal_phrase_model(chat_id)
        
        with app.app_context():
            # Подсчитываем количество записей перед удалением
            count_before = db.session.query(PhraseModel).count()
            
            if count_before == 0:
                send_message(chat_id, "Ваша таблица phrases уже пуста.")
                return

            # Удаляем все записи
            deleted_count = db.session.query(PhraseModel).delete()
            db.session.commit()
            
            logger.info(f"Удалено {deleted_count} записей из phrases_{chat_id}")
            send_message(chat_id, f"✅ Таблица phrases успешно очищена. Удалено записей: {deleted_count}")

    except Exception as e:
        logger.error(f"Ошибка в задаче clear_phrases для пользователя {chat_id}: {e}")
        with app.app_context():
            db.session.rollback()
        send_message(chat_id, f"❌ Ошибка при очистке таблицы phrases: {e}")

# --- Telegram Webhook ---
@app.route(f"/{TELEGRAM_TOKEN}", methods=["POST"])
def webhook():
    """Обработчик вебхука Telegram."""
    initialize_database_if_needed()

    # ИСПРАВЛЕНО: Полное и корректное условие проверки JSON-данных
    json_data = request.get_json()
    if not json_data: # Проверяем, что json_data не None и не пустой
        logger.warning("Получен не JSON запрос или пустое тело")
        return "OK"

    message = json_data.get("message")
    if not message:
        return "OK"

    chat_id = message.get("chat", {}).get("id")
    if not chat_id:
        logger.warning("Не удалось получить chat_id")
        return "OK"

    try:
        if "text" in message:
            text = message["text"]
            if text == "/start":
                cache_key = str(chat_id)
                if cache_key in _user_shop_cache:
                    user_exists = _user_shop_cache[cache_key]
                else:
                    with app.app_context():
                        user_exists = db.session.query(User.id).filter_by(chat_id=str(chat_id)).first() is not None
                    
                base_msg = "Привет! "
                if user_exists:
                    base_msg += "Вы есть в базе."
                else:
                    username = message.get("from", {}).get("username", "Неизвестный")
                    with app.app_context():
                        new_user = User(chat_id=str(chat_id), username=username)
                        try:
                            db.session.add(new_user)
                            db.session.commit()
                            logger.info(f"Новый пользователь {username} ({chat_id}) зарегистрирован.")
                            _user_shop_cache.pop(cache_key, None)
                            base_msg += "Вы зарегистрированы в базе."
                        except Exception as e:
                            db.session.rollback()
                            logger.error(f"Ошибка регистрации пользователя {chat_id}: {e}")
                            base_msg += "Произошла ошибка при регистрации."

                # --- ИЗМЕНЕНО: Добавлены новые строки в меню ---
                menu_msg = (
                    f"{base_msg}\n"
                    f"/words - сканирование слов\n"
                    f"/searchads - поиск реклам по словам\n"
                    f"/clearwords - очистка фраз"
                )
                send_message(chat_id, menu_msg)

            elif text == "/words":
                send_message(chat_id, "Пожалуйста, отправьте ZIP файл с XLSX аналитики поиска.")

            elif text == "/searchads":
                thread = threading.Thread(target=search_ads_task, args=(chat_id,))
                thread.start()
                send_message(chat_id, "Команда /searchads принята. Задача запущена в фоновом режиме. Результаты будут отправлены по мере обработки.")

            # --- НОВАЯ КОМАНДА ---
            elif text == "/clearwords":
                # Запускаем задачу очистки в фоновом потоке
                thread = threading.Thread(target=clear_phrases_task, args=(chat_id,))
                thread.start()
                send_message(chat_id, "Команда /clearwords принята. Задача очистки запущена.")

        elif "document" in message:
            doc = message["document"]
            if doc.get("file_name", "").lower().endswith('.zip'):
                try:
                    file_id = doc["file_id"]
                    file_info_res = requests.get(
                        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile",
                        params={"file_id": file_id},
                        timeout=15
                    )
                    file_info_res.raise_for_status()
                    file_path = file_info_res.json()["result"]["file_path"]

                    file_content_res = requests.get(
                        f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}",
                        timeout=30
                    )
                    file_content_res.raise_for_status()

                    result_msg = process_zip_and_xlsx(file_content_res.content, doc["file_name"], chat_id)
                    send_message(chat_id, result_msg)

                except requests.exceptions.RequestException as e:
                    error_msg = f"Ошибка загрузки файла из Telegram: {e}"
                    logger.error(error_msg)
                    send_message(chat_id, error_msg)
                except Exception as e:
                    error_msg = f"Ошибка обработки файла: {e}"
                    logger.error(error_msg)
                    send_message(chat_id, error_msg)
            else:
                send_message(chat_id, "Пожалуйста, отправьте файл в формате ZIP.")

    except Exception as e:
        logger.error(f"Ошибка в webhook: {e}\n{traceback.format_exc()}")
        # Не отправляем сообщение об ошибке пользователю в webhook

    return "OK"

# --- Другие маршруты ---
@app.route("/")
def index():
    """Главная страница с информацией."""
    initialize_database_if_needed()

    try:
        with app.app_context():
            tables_exist = _check_tables_exist()
            if not tables_exist:
                 return "<h1>Бот Tittle</h1><p>Инициализация БД...</p>"

            inspector = inspect(db.engine)
            tables = inspector.get_table_names()

            counts = {}
            if 'users' in tables:
                result = db.session.execute(text("SELECT COUNT(*) FROM users")).scalar()
                counts['users'] = result or 0
            
            personal_tables = [t for t in tables if t.startswith('phrases_') or t.startswith('shops_')]
            counts['personal_tables'] = len(personal_tables)

        html = "<h1>Бот Tittle работает!</h1><ul>"
        for table, count in counts.items():
            html += f"<li>{table.capitalize()}: {count}</li>"
        html += "</ul>"
        return html
    except Exception as e:
        logger.error(f"Ошибка на главной странице: {e}")
        return f"<h1>Ошибка</h1><p>{str(e)}</p>", 500

# --- Инициализация ---
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)
