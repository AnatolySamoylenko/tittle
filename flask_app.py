# flask_app.py
import os
import re
import zipfile
import traceback
import logging
from io import BytesIO

import requests
import pandas as pd
from flask import Flask, request
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text, MetaData, Table
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
if not TEGRAM_TOKEN:
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
    # Новые поля
    preset = db.Column(db.Integer, nullable=False, default=0)
    normQuery = db.Column(db.Text, nullable=True, default=None)
    auto = db.Column(db.Integer, nullable=False, default=0)
    auction = db.Column(db.Integer, nullable=False, default=0)
    total = db.Column(db.Integer, nullable=False, default=0)

# --- Вспомогательные функции ---
def _check_and_update_phrases_table():
    """Проверяет структуру таблицы phrases и обновляет её при необходимости."""
    with app.app_context():
        try:
            inspector = inspect(db.engine)
            if 'phrases' not in inspector.get_table_names():
                logger.info("Таблица 'phrases' не найдена. Будет создана при инициализации БД.")
                return False # Таблица отсутствует

            # Получаем информацию о существующих колонках
            existing_columns = {col['name'] for col in inspector.get_columns('phrases')}
            logger.debug(f"Существующие колонки в 'phrases': {existing_columns}")
            
            # Получаем ожидаемые колонки из модели
            # Создаем временную таблицу на основе модели, чтобы получить её структуру
            temp_metadata = MetaData()
            temp_table = Phrase.__table__.to_metadata(temp_metadata)
            expected_columns = {col.name for col in temp_table.columns}
            logger.debug(f"Ожидаемые колонки в 'phrases': {expected_columns}")
            
            # Проверяем, совпадают ли множества колонок
            if existing_columns == expected_columns:
                logger.info("Структура таблицы 'phrases' соответствует модели.")
                return True # Структура совпадает
            else:
                logger.warning(f"Несовпадение структуры таблицы 'phrases'. "
                               f"Существующие: {existing_columns}. Ожидаемые: {expected_columns}. "
                               f"Таблица будет пересоздана.")
                # Удаляем таблицу
                db.session.execute(text("DROP TABLE phrases"))
                db.session.commit()
                _tables_exist_cache.clear() # Сбрасываем кэш
                return False # Таблица была удалена
                
        except Exception as e:
            logger.error(f"Ошибка при проверке/обновлении таблицы 'phrases': {e}")
            # В случае ошибки проверки, лучше пересоздать таблицу
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
        # Для phrases делаем отдельную проверку с возможным обновлением
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

def process_phrases_from_xlsx(df, chat_id):
    """
    Обрабатывает DataFrame с данными фраз и сохраняет их в БД.
    Использует колонки: 0 (Поисковый запрос), 3 (Запросов в среднем за день), 5 (Больше всего заказов в предмете).
    """
    logger.info(f"Начинаем обработку DataFrame. Форма: {df.shape}, Колонки: {list(df.columns)[:10]}...")

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

        # Очистка данных
        data_slice['phrase'] = data_slice['phrase_raw'].astype(str).str.strip()
        data_slice = data_slice[data_slice['phrase'] != '']
        data_slice['qntyPerDay'] = pd.to_numeric(data_slice['qntyPerDay_raw'], errors='coerce').fillna(0).astype(int)
        data_slice['subject'] = data_slice['subject_raw'].astype(str).str.strip()

        final_data = data_slice.drop(columns=['phrase_raw', 'qntyPerDay_raw', 'subject_raw'])
        logger.info(f"Данные после очистки: {final_data.shape[0]} строк.")

        if final_data.empty:
            raise ValueError("Нет данных для импорта после очистки.")

        with app.app_context():
            # Используем ORM-запросы для проверки и удаления
            phrases_in_file = set(final_data['phrase'].tolist())
            existing_phrases = set()
            if phrases_in_file:
                existing_records = db.session.query(Phrase.phrase).filter(
                    Phrase.phrase.in_(list(phrases_in_file))
                ).all()
                existing_phrases = {row[0] for row in existing_records}
                logger.debug(f"Найдено существующих фраз в БД: {len(existing_phrases)}")

            phrases_to_add = []
            new_phrases_info = []

            for _, row in final_data.iterrows():
                phrase_text = row['phrase']
                is_new = phrase_text not in existing_phrases

                phrase_obj = Phrase(
                    phrase=phrase_text,
                    qntyPerDay=row['qntyPerDay'],
                    subject=row['subject']
                )
                phrases_to_add.append(phrase_obj)

                if is_new:
                    new_phrases_info.append({
                        'phrase': phrase_text,
                        'qntyPerDay': row['qntyPerDay'],
                        'subject': row['subject']
                    })

            # Массовое удаление существующих фраз
            if existing_phrases:
                logger.debug(f"Удаление {len(existing_phrases)} существующих фраз...")
                db.session.query(Phrase).filter(
                    Phrase.phrase.in_(list(existing_phrases))
                ).delete(synchronize_session=False)

            # Массовая вставка всех фраз
            if phrases_to_add:
                logger.debug(f"Массовая вставка {len(phrases_to_add)} фраз...")
                db.session.bulk_save_objects(phrases_to_add, update_changed_only=False)

            db.session.commit()
            logger.info("Данные успешно сохранены в БД.")

            phrases_added = len(new_phrases_info)
            phrases_updated = len(existing_phrases)

            # Отправка информации о новых фразах
            if new_phrases_info:
                logger.debug(f"Отправляем информацию о {len(new_phrases_info)} новых фразах...")
                info_to_send = new_phrases_info[:50]
                for i in range(0, len(info_to_send), 10):
                    batch = info_to_send[i:i+10]
                    message_lines = [f"Найдены новые фразы ({len(new_phrases_info)} всего, показаны первые {len(info_to_send)}):"]
                    for info in batch:
                        message_lines.append(
                            f"Фраза: {info['phrase']}\n"
                            f"Запросов/день: {info['qntyPerDay']}\n"
                            f"Предмет: {info['subject']}\n"
                            f"---"
                        )
                    send_message(chat_id, "\n".join(message_lines))

                if len(new_phrases_info) > 50:
                     send_message(chat_id, f"... и еще {len(new_phrases_info) - 50} фраз.")

            return phrases_added, phrases_updated, len(final_data)

    except Exception as e:
        logger.error(f"Ошибка при обработке данных DataFrame: {e}")
        db.session.rollback()
        raise


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


# --- Telegram Webhook ---
@app.route(f"/{TELEGRAM_TOKEN}", methods=["POST"])
def webhook():
    """Обработчик вебхука Telegram."""
    initialize_database_if_needed()
    
    json_data = request.get_json()
    if not json_
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
# Инициализация будет происходить лениво при первом запросе к / или webhook

if __name__ == "__main__":
    # Для локального запуска
    with app.app_context():
        db.create_all()
    app.run(debug=True)
