# flask_app.py
import os
import re
import zipfile
import traceback
import logging
import time # Для пауз между запросами
import json # Для работы с JSON ответами
import random # Для генерации случайной задержки
import threading # Для запуска длительной задачи в фоне
from io import BytesIO

import requests
import pandas as pd
from flask import Flask, request
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text, MetaData
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

# --- Модели БД ---
class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    chat_id = db.Column(db.String(50), unique=True, nullable=False)
    username = db.Column(db.String(100))

class Shop(db.Model):
    __tablename__ = 'shops'
    shopId = db.Column(db.Integer, primary_key=True, autoincrement=False)
    API = db.Column(db.Text, nullable=False)
    chatId = db.Column(db.Integer, nullable=False)

class Phrase(db.Model):
    __tablename__ = 'phrases'
    phrase = db.Column(db.Text, primary_key=True, nullable=False)
    qntyPerDay = db.Column(db.Integer, nullable=False)
    subject = db.Column(db.Text, nullable=False)
    # Новые поля (убраны default из создания объектов)
    preset = db.Column(db.Integer, nullable=False) # default=0 убрано
    normQuery = db.Column(db.Text, nullable=True) # default=None убрано
    auto = db.Column(db.Integer, nullable=False) # default=0 убрано
    auction = db.Column(db.Integer, nullable=False) # default=0 убрано
    total = db.Column(db.Integer, nullable=False) # default=0 убрано

# --- Вспомогательные функции ---
def _check_and_update_phrases_table():
    """Проверяет структуру таблицы phrases и обновляет её при необходимости."""
    with app.app_context():
        try:
            inspector = inspect(db.engine)
            if 'phrases' not in inspector.get_table_names():
                logger.info("Таблица 'phrases' не найдена. Будет создана при инициализации БД.")
                return False

            existing_columns = {col['name'] for col in inspector.get_columns('phrases')}
            logger.debug(f"Существующие колонки в 'phrases': {existing_columns}")

            temp_metadata = MetaData()
            temp_table = Phrase.__table__.to_metadata(temp_metadata)
            expected_columns = {col.name for col in temp_table.columns}
            logger.debug(f"Ожидаемые колонки в 'phrases': {expected_columns}")

            if existing_columns == expected_columns:
                logger.info("Структура таблицы 'phrases' соответствует модели.")
                return True
            else:
                logger.warning(f"Несовпадение структуры таблицы 'phrases'. "
                               f"Существующие: {existing_columns}. Ожидаемые: {expected_columns}. "
                               f"Таблица будет пересоздана.")
                db.session.execute(text("DROP TABLE phrases"))
                db.session.commit()
                _tables_exist_cache.clear()
                return False

        except Exception as e:
            logger.error(f"Ошибка при проверке/обновлении таблицы 'phrases': {e}")
            try:
                db.session.execute(text("DROP TABLE IF EXISTS phrases"))
                db.session.commit()
                _tables_exist_cache.clear()
                logger.info("Таблица 'phrases' будет пересоздана из-за ошибки проверки.")
            except Exception as drop_e:
                logger.error(f"Ошибка при принудительном удалении таблицы 'phrases': {drop_e}")
            return False

def _check_tables_exist():
    """Проверяет существование таблиц, используя кэш."""
    global _tables_exist_cache
    cache_key = "tables_exist"
    if cache_key in _tables_exist_cache:
        return _tables_exist_cache[cache_key]

    try:
        inspector = inspect(db.engine)
        existing_tables = set(inspector.get_table_names())
        required_tables = {'users', 'shops'}
        phrases_ok = _check_and_update_phrases_table()

        result = required_tables.issubset(existing_tables) and phrases_ok
        _tables_exist_cache[cache_key] = result
        if not result:
            missing = required_tables - existing_tables
            if missing:
                logger.info(f"Отсутствующие таблицы: {missing}")
            if not phrases_ok:
                logger.info("Таблица 'phrases' требует пересоздания или создания.")
        return result
    except Exception as e:
        logger.error(f"Ошибка при проверке таблиц: {e}")
        return False

def initialize_database_if_needed():
    """Ленивая инициализация БД при первом обращении."""
    if not _check_tables_exist():
        logger.info("Таблицы отсутствуют или имеют неактуальную структуру, инициализируем БД...")
        with app.app_context():
            try:
                db.create_all()
                _tables_exist_cache.clear()
                logger.info("Таблицы успешно созданы или обновлены.")
            except Exception as e:
                logger.error(f"Ошибка при создании/обновлении таблиц: {e}")
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

# --- ОБНОВЛЕННАЯ ФУНКЦИЯ: Построчная обработка XLSX ---
def process_phrases_from_xlsx(df, chat_id):
    """
    Обрабатывает DataFrame с данными фраз и сохраняет их в БД ПОСТРОЧНО.
    Использует колонки: 0 (Поисковый запрос), 3 (Запросов в среднем за день), 5 (Больше всего заказов в предмете).
    """
    logger.info(f"Начинаем ПОСТРОЧНУЮ обработку DataFrame. Форма: {df.shape}")

    required_indices = {'phrase_col_idx': 0, 'qnty_col_idx': 3, 'subject_col_idx': 5}
    max_required_idx = max(required_indices.values())

    if max_required_idx >= len(df.columns):
        raise ValueError(
            f"Файл не содержит достаточно колонок. "
            f"Требуется как минимум {max_required_idx + 1} колонок. Найдено {len(df.columns)}."
        )

    # Выбираем нужные колонки
    try:
        data_slice = df.iloc[:, [required_indices['phrase_col_idx'],
                                 required_indices['qnty_col_idx'],
                                 required_indices['subject_col_idx']]].copy()
        data_slice.columns = ['phrase_raw', 'qntyPerDay_raw', 'subject_raw']
    except Exception as e:
        logger.error(f"Ошибка при выборе колонок: {e}")
        raise ValueError(f"Ошибка при выборе колонок: {e}")

    total_rows = len(data_slice)
    logger.info(f"Будет обработано {total_rows} строк.")

    if total_rows == 0:
        return 0, 0, 0 # phrases_added, phrases_updated, total_processed

    phrases_added = 0
    phrases_updated = 0
    processed_count = 0

    with app.app_context():
        try:
            # Итерируемся по строкам DataFrame
            for index, row in data_slice.iterrows():
                processed_count += 1
                # --- Обработка одной строки ---
                try:
                    # 1. Очистка данных для текущей строки
                    phrase_raw = row['phrase_raw']
                    phrase = str(phrase_raw).strip() if pd.notna(phrase_raw) else ""
                    
                    if not phrase:
                        logger.debug(f"[{processed_count}/{total_rows}] Пропущена пустая фраза в строке {index}")
                        continue

                    qntyPerDay_raw = row['qntyPerDay_raw']
                    qntyPerDay = int(float(qntyPerDay_raw)) if pd.notna(qntyPerDay_raw) else 0
                    
                    subject_raw = row['subject_raw']
                    subject = str(subject_raw).strip() if pd.notna(subject_raw) else ""

                    # 2. Проверка существования фразы в БД
                    existing_phrase = db.session.get(Phrase, phrase)
                    is_new = existing_phrase is None

                    # 3. Создание или обновление объекта
                    if is_new:
                        # Создаем новую фразу
                        phrase_obj = Phrase(
                            phrase=phrase,
                            qntyPerDay=qntyPerDay,
                            subject=subject
                            # preset, normQuery, auto, auction, total будут установлены позже (по умолчанию 0/None)
                        )
                        db.session.add(phrase_obj)
                        phrases_added += 1
                        logger.debug(f"[{processed_count}/{total_rows}] Добавлена новая фраза: '{phrase}'")
                    else:
                        # Удаляем существующую и добавляем обновленную
                        db.session.delete(existing_phrase)
                        new_phrase_obj = Phrase(
                            phrase=phrase,
                            qntyPerDay=qntyPerDay,
                            subject=subject
                        )
                        db.session.add(new_phrase_obj)
                        phrases_updated += 1
                        logger.debug(f"[{processed_count}/{total_rows}] Обновлена фраза: '{phrase}'")

                    # 4. Коммитим изменения для каждой строки
                    # Это позволяет избежать накопления большого количества изменений в сессии
                    db.session.commit()
                    
                    # 5. Отправка уведомления о новой фразе (только для новых)
                    if is_new:
                         message_text = f"Новая фраза:\nФраза: {phrase}\nЗапросов в день: {qntyPerDay}\nПредмет: {subject}"
                         send_message(chat_id, message_text)

                except (ValueError, TypeError) as row_e:
                    logger.error(f"[{processed_count}/{total_rows}] Ошибка обработки строки {index}: {row_e}")
                    db.session.rollback() # Откатываем транзакцию для этой строки
                    # Продолжаем обработку следующей строки
                    continue
                except Exception as row_e:
                    logger.error(f"[{processed_count}/{total_rows}] Неожиданная ошибка в строке {index}: {row_e}")
                    db.session.rollback()
                    continue

                # --- Отправка промежуточного отчета ---
                if processed_count % 100 == 0 or processed_count == total_rows:
                     progress_msg = f"Обработано {processed_count} из {total_rows} строк. Добавлено: {phrases_added}, Обновлено: {phrases_updated}"
                     logger.info(progress_msg)
                     # Отправляем сообщение каждые 1000 строк или в конце
                     if processed_count % 1000 == 0 or processed_count == total_rows:
                         send_message(chat_id, progress_msg)

        except Exception as e:
            logger.error(f"Критическая ошибка в процессе обработки: {e}")
            db.session.rollback()
            # Даже при критической ошибке, часть данных могла быть сохранена
            final_msg = f"Задача прервана. Обработано {processed_count} строк. Добавлено: {phrases_added}, Обновлено: {phrases_updated}. Ошибка: {e}"
            send_message(chat_id, final_msg)
            # Перебрасываем исключение, чтобы оно отобразилось в основном обработчике
            raise 

    logger.info(f"Построчная обработка завершена. Всего: {total_rows}, Добавлено: {phrases_added}, Обновлено: {phrases_updated}")
    return phrases_added, phrases_updated, processed_count


def process_zip_and_xlsx(zip_content, original_filename, chat_id):
    """Распаковывает ZIP, находит XLSX, читает данные и сохраняет фразы."""
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
                df = pd.read_excel(xlsx_file, sheet_name=target_sheet, header=3)

            if df.empty:
                return "Ошибка: Лист с данными пуст."

            added, updated, total = process_phrases_from_xlsx(df, chat_id)
            return f"Импорт завершен.\nДобавлено: {added}\nОбновлено: {updated}\nВсего обработано: {total}"

    except zipfile.BadZipFile:
        return "Ошибка: Файл не является корректным ZIP архивом."
    except ValueError as e:
        return f"Ошибка данных: {e}"
    except Exception as e:
        logger.error(f"Неожиданная ошибка: {e}\n{traceback.format_exc()}")
        return f"Ошибка: {str(e)}"

# --- НОВАЯ ФУНКЦИЯ: Логика команды /searchads ---
def search_ads_task(chat_id):
    """
    Фоновая задача для выполнения поисковых запросов и обновления данных в БД.
    """
    logger.info(f"Запуск задачи search_ads для chat_id: {chat_id}")
    send_message(chat_id, "Начинаю выполнение /searchads. Это может занять некоторое время...")

    try:
        with app.app_context():
            # 1. Получаем все фразы из БД
            phrases = db.session.query(Phrase).all()
            logger.info(f"Найдено {len(phrases)} фраз для обработки.")

            if not phrases:
                send_message(chat_id, "В таблице phrases нет данных для обработки.")
                return

            base_url = "https://search.wb.ru/exactmatch/ru/common/v14/search"
            params_template = {
                "ab_testing": "false",
                "appType": "32",
                "curr": "rub",
                "dest": "-1257484",
                "lang": "ru",
                "page": "1", # Используем первую страницу
                "resultset": "catalog",
                "sort": "popular",
                "spp": "30",
                "suppressSpellcheck": "false"
            }

            updated_count = 0
            errors = []

            for i, phrase_obj in enumerate(phrases):
                phrase_text = phrase_obj.phrase
                logger.debug(f"[{i+1}/{len(phrases)}] Обрабатываем фразу: '{phrase_text}'")

                # Отправляем промежуточное сообщение каждые 100 фраз
                if (i + 1) % 100 == 0:
                    send_message(chat_id, f"Обработано {i+1} из {len(phrases)} фраз...")

                # Формируем параметры запроса
                params = params_template.copy()
                params["query"] = phrase_text

                # Логика повторных запросов
                max_total_retries = 5
                max_preset_retries = 3
                total_retries = 0
                preset_retries = 0
                success = False
                response_data = None

                while not success and (total_retries < max_total_retries or preset_retries < max_preset_retries):
                    try:
                        # Пауза от 3 до 7 секунд
                        delay = random.uniform(3, 7)
                        logger.debug(f"Пауза {delay:.2f} секунд перед запросом...")
                        time.sleep(delay)

                        response = requests.get(base_url, params=params, timeout=15)
                        response.raise_for_status()
                        response_data = response.json()

                        metadata = response_data.get("metadata", {})
                        total = response_data.get("total", 0)
                        preset = metadata.get("presetId", 0)

                        logger.debug(f"Ответ для '{phrase_text}': total={total}, preset={preset}")

                        # Проверка условий повтора
                        if total == 0 and total_retries < max_total_retries:
                            total_retries += 1
                            logger.info(f"Total=0 для '{phrase_text}', повтор ({total_retries}/{max_total_retries})...")
                            continue # Повторить запрос

                        if preset == 0 and preset_retries < max_preset_retries:
                            preset_retries += 1
                            logger.info(f"Preset=0 для '{phrase_text}', повтор ({preset_retries}/{max_preset_retries})...")
                            continue # Повторить запрос

                        # Если дошли до этой точки, значит условия повтора не выполняются
                        success = True

                    except requests.exceptions.RequestException as e:
                        logger.error(f"Ошибка запроса для фразы '{phrase_text}': {e}")
                        errors.append(f"Фраза '{phrase_text}': Ошибка запроса - {e}")
                        break # Не повторяем при сетевых ошибках в рамках этой задачи
                    except json.JSONDecodeError as e:
                        logger.error(f"Ошибка парсинга JSON для фразы '{phrase_text}': {e}")
                        errors.append(f"Фраза '{phrase_text}': Ошибка парсинга JSON - {e}")
                        break
                    except Exception as e:
                        logger.error(f"Неожиданная ошибка для фразы '{phrase_text}': {e}")
                        errors.append(f"Фраза '{phrase_text}': Неожиданная ошибка - {e}")
                        break

                if not success:
                    logger.warning(f"Не удалось получить корректные данные для фразы '{phrase_text}' после повторов.")
                    errors.append(f"Фраза '{phrase_text}': Не удалось получить данные после повторов.")
                    # Продолжаем со следующей фразой
                    continue

                # --- Обработка успешного ответа ---
                try:
                    # Подсчет tp
                    tpc = 0 # tp="c"
                    tpb = 0 # tp="b"
                    products = response_data.get("products", [])
                    for product in products:
                        log = product.get("log", {})
                        tp = log.get("tp")
                        if tp == "c":
                            tpc += 1
                        elif tp == "b":
                            tpb += 1

                    # Получение других данных
                    metadata = response_data.get("metadata", {})
                    total = response_data.get("total", 0)
                    norm_query = metadata.get("normquery")
                    preset_id = metadata.get("presetId", 0) # Используем из ответа

                    logger.debug(f"Результаты для '{phrase_text}': tpc={tpc}, tpb={tpb}, total={total}, normQuery='{norm_query}', preset={preset_id}")

                    # Обновление записи в БД
                    phrase_obj.auction = tpc
                    phrase_obj.auto = tpb
                    phrase_obj.total = total
                    phrase_obj.normQuery = norm_query
                    phrase_obj.preset = preset_id # Обновляем поле preset

                    db.session.commit() # Коммитим каждую запись
                    updated_count += 1

                    # Отправка результата в Telegram
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
                    logger.error(f"Ошибка обработки/обновления данных для фразы '{phrase_text}': {e}")
                    errors.append(f"Фраза '{phrase_text}': Ошибка обработки данных - {e}")
                    db.session.rollback() # Откатываем в случае ошибки обновления

            # --- Финальный отчет ---
            final_message_lines = [f"✅ Задача /searchads завершена."]
            final_message_lines.append(f"   Обработано фраз: {len(phrases)}")
            final_message_lines.append(f"   Успешно обновлено: {updated_count}")

            if errors:
                final_message_lines.append(f"   Ошибок: {len(errors)}")
                # Отправляем первые 5 ошибок
                for err in errors[:5]:
                    final_message_lines.append(f"   - {err}")
                if len(errors) > 5:
                    final_message_lines.append(f"   ... и еще {len(errors) - 5} ошибок.")

            send_message(chat_id, "\n".join(final_message_lines))

    except Exception as e:
        logger.error(f"Критическая ошибка в задаче search_ads: {e}")
        send_message(chat_id, f"❌ Критическая ошибка в задаче /searchads: {e}")


# --- Telegram Webhook (исправленный фрагмент) ---
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

    # ... (остальной код функции webhook остается без изменений) ...
    # --- Конец исправленного фрагмента ---

    try:
        if "text" in message:
            text = message["text"]
            if text == "/start":
                cache_key = str(chat_id)
                if cache_key in _user_shop_cache:
                    user_exists, shop_exists = _user_shop_cache[cache_key]
                else:
                    with app.app_context():
                        user_exists = db.session.query(User.id).filter_by(chat_id=str(chat_id)).first() is not None
                        shop_exists = db.session.query(Shop.shopId).filter_by(chatId=chat_id).first() is not None

                base_msg = "Привет! "
                if user_exists:
                    base_msg += "Вы есть в базе"
                    if shop_exists:
                        base_msg += " и у вас есть зарегистрированный магазин."
                    else:
                        base_msg += ", но у вас нет зарегистрированных магазинов."
                else:
                    username = message.get("from", {}).get("username", "Неизвестный")
                    with app.app_context():
                        new_user = User(chat_id=str(chat_id), username=username)
                        try:
                            db.session.add(new_user)
                            db.session.commit()
                            logger.info(f"Новый пользователь {username} ({chat_id}) зарегистрирован.")
                            _user_shop_cache.pop(cache_key, None)
                            base_msg += "Вы зарегистрированы в базе"
                            base_msg += ", но у вас нет зарегистрированных магазинов."
                        except Exception as e:
                            db.session.rollback()
                            logger.error(f"Ошибка регистрации пользователя {chat_id}: {e}")
                            base_msg += "Произошла ошибка при регистрации."

                send_message(chat_id, f"{base_msg}\n/words сканирование слов")

            elif text == "/words":
                send_message(chat_id, "Пожалуйста, отправьте ZIP файл с XLSX аналитики поиска.")

            # --- НОВАЯ КОМАНДА ---
            elif text == "/searchads":
                # Запускаем длительную задачу в фоновом потоке
                # чтобы не блокировать вебхук
                thread = threading.Thread(target=search_ads_task, args=(chat_id,))
                thread.start()
                send_message(chat_id, "Команда /searchads принята. Задача запущена в фоновом режиме. Результаты будут отправлены по мере обработки.")

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
            if 'shops' in tables:
                result = db.session.execute(text("SELECT COUNT(*) FROM shops")).scalar()
                counts['shops'] = result or 0
            if 'phrases' in tables:
                result = db.session.execute(text("SELECT COUNT(*) FROM phrases")).scalar()
                counts['phrases'] = result or 0

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
