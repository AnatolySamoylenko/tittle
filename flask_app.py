# flask_app.py
import os
import re
import zipfile
import traceback
from io import BytesIO

import requests
import pandas as pd
from flask import Flask, request
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect

app = Flask(__name__)

# --- Конфигурация ---
# Убедитесь, что путь корректный и доступен для записи
DB_PATH = os.path.join(os.path.expanduser('~'), 'tittle_database.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DB_PATH}'
# Отключаем отслеживание изменений моделей для экономии ресурсов
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')

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

# Модель фраз без id и дат
class Phrase(db.Model):
    __tablename__ = 'phrases'
    # phrase является первичным ключом, обеспечивая уникальность
    phrase = db.Column(db.Text, primary_key=True, nullable=False)
    qntyPerDay = db.Column(db.Integer, nullable=False)
    subject = db.Column(db.Text, nullable=False)

# --- Вспомогательные функции ---
def initialize_database():
    """Создает таблицы БД при запуске, если они не существуют."""
    with app.app_context():
        inspector = inspect(db.engine)
        existing_tables = set(inspector.get_table_names())

        required_tables = {'users', 'shops', 'phrases'}
        tables_to_create = required_tables - existing_tables

        if tables_to_create:
            print(f"Создаем таблицы: {', '.join(tables_to_create)}")
            try:
                # Создаем только недостающие таблицы
                db.create_all()
                print("Таблицы успешно созданы.")
            except Exception as e:
                print(f"Ошибка при создании таблиц: {e}")
        else:
            print("Все таблицы уже существуют.")

def send_message(chat_id, text):
    """Отправляет текстовое сообщение в Telegram."""
    if not TELEGRAM_TOKEN:
        print("TELEGRAM_TOKEN не установлен!")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
        app.logger.info(f"Сообщение отправлено пользователю {chat_id}")
    except requests.exceptions.RequestException as e:
        app.logger.error(f"Ошибка отправки сообщения пользователю {chat_id}: {e}")

# --- Основная логика обработки файлов ---
def extract_dates_from_filename_simple(filename):
    """Простое извлечение дат для логирования. Не используется в БД."""
    # Упрощенное регулярное выражение
    pattern = r'[сcCc][\s_\-\.]*([\d\-\./]+)[\s_\-\.]*[пnN][оoO][\s_\-\.]*([\d\-\./]+)'
    match = re.search(pattern, filename)
    if match:
        return match.group(1), match.group(2)
    return None, None

def process_phrases_from_xlsx(df, chat_id):
    """
    Обрабатывает DataFrame с данными фраз и сохраняет их в БД.
    Отправляет новые фразы в Telegram.
    """
    required_columns = {
        'phrase_col': None,
        'qnty_col': None,
        'subject_col': None
    }

    # Поиск колонок по названию
    for col in df.columns:
        col_clean = str(col).strip().lower()
        if 'поисковый запрос' in col_clean:
            required_columns['phrase_col'] = col
        elif 'количество запросов' in col_clean and 'среднем' not in col_clean:
            required_columns['qnty_col'] = col
        elif 'больше всего заказов' in col_clean:
            required_columns['subject_col'] = col

    # Проверка наличия всех необходимых колонок
    if None in required_columns.values():
        missing = [k for k, v in required_columns.items() if v is None]
        raise ValueError(f"Не найдены колонки: {missing}")

    # Выбор и очистка нужных данных
    data_slice = df[list(required_columns.values())].copy()
    data_slice.columns = ['phrase_raw', 'qntyPerDay_raw', 'subject_raw']

    # Очистка и преобразование данных
    data_slice['phrase'] = data_slice['phrase_raw'].astype(str).str.strip()
    data_slice = data_slice[data_slice['phrase'] != '']
    data_slice['qntyPerDay'] = pd.to_numeric(data_slice['qntyPerDay_raw'], errors='coerce').fillna(0).astype(int)
    data_slice['subject'] = data_slice['subject_raw'].astype(str).str.strip()

    # Удаление промежуточных колонок
    data_slice.drop(columns=['phrase_raw', 'qntyPerDay_raw', 'subject_raw'], inplace=True)

    if data_slice.empty:
        raise ValueError("Нет данных для импорта после очистки.")

    phrases_added = 0
    phrases_updated = 0
    new_phrases_info = [] # Для сбора информации о новых фразах

    with app.app_context():
        try:
            # Обработка каждой строки
            phrases_to_merge = []
            for _, row in data_slice.iterrows():
                phrase_text = row['phrase']
                # Проверяем существование фразы
                existing_phrase = db.session.get(Phrase, phrase_text)

                if existing_phrase:
                    phrases_updated += 1
                else:
                    phrases_added += 1
                    # Собираем информацию о новой фразе
                    new_phrases_info.append({
                        'phrase': phrase_text,
                        'qntyPerDay': row['qntyPerDay'],
                        'subject': row['subject']
                    })

                # Создаем объект для merge
                phrase_obj = Phrase(
                    phrase=phrase_text,
                    qntyPerDay=row['qntyPerDay'],
                    subject=row['subject']
                )
                phrases_to_merge.append(phrase_obj)

            # Массовое обновление/вставка
            for obj in phrases_to_merge:
                db.session.merge(obj)
            db.session.commit()

            # Отправка информации о новых фразах
            if new_phrases_info:
                # Отправляем сообщения пакетами по 10, чтобы не перегружать Telegram
                for i in range(0, len(new_phrases_info), 10):
                    batch = new_phrases_info[i:i+10]
                    message_lines = [f"Найдено {len(new_phrases_info)} новых фраз:"]
                    for info in batch:
                        message_lines.append(
                            f"Фраза: {info['phrase']}\n"
                            f"Запросов/день: {info['qntyPerDay']}\n"
                            f"Предмет: {info['subject']}\n"
                            f"---"
                        )
                    send_message(chat_id, "\n".join(message_lines))

        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Ошибка БД при обработке фраз: {e}")
            raise

    return phrases_added, phrases_updated, len(data_slice)

def process_zip_and_xlsx(zip_content, original_filename, chat_id):
    """
    Распаковывает ZIP, находит XLSX, читает данные и сохраняет фразы.
    """
    try:
        dates_start, dates_end = extract_dates_from_filename_simple(original_filename)
        app.logger.info(f"Даты из имени файла (для инфо): {dates_start} - {dates_end}")

        with zipfile.ZipFile(BytesIO(zip_content)) as zip_ref:
            # Поиск XLSX файла
            xlsx_filename = None
            for f in zip_ref.namelist():
                if "аналитика поиска" in f.lower() and f.lower().endswith('.xlsx'):
                    xlsx_filename = f
                    break
            if not xlsx_filename:
                 # fallback на любой .xlsx
                for f in zip_ref.namelist():
                    if f.lower().endswith('.xlsx'):
                        xlsx_filename = f
                        app.logger.info(f"XLSX найден по расширению: {xlsx_filename}")
                        break

            if not xlsx_filename:
                return "Ошибка: В ZIP архиве не найден файл .xlsx."

            # Чтение XLSX
            with zip_ref.open(xlsx_filename) as xlsx_file:
                # Поиск листа "Детальная информация"
                excel_file = pd.ExcelFile(xlsx_file)
                target_sheet = None
                for name in excel_file.sheet_names:
                    if "детальная информация" in name.lower():
                        target_sheet = name
                        break
                if not target_sheet and len(excel_file.sheet_names) > 2:
                    target_sheet = excel_file.sheet_names[2] # fallback на 3-й лист
                    app.logger.info(f"Лист 'Детальная информация' не найден, используем: {target_sheet}")

                if not target_sheet:
                     return f"Ошибка: Не найден лист с данными. Доступны: {excel_file.sheet_names}"

                # Считываем данные с 4-й строки как заголовки
                xlsx_file.seek(0) # Сброс позиции файла
                df = pd.read_excel(xlsx_file, sheet_name=target_sheet, header=3)

            if df.empty:
                return "Ошибка: Лист с данными пуст."

            # Обработка данных
            added, updated, total = process_phrases_from_xlsx(df, chat_id)
            return f"Импорт завершен.\nДобавлено: {added}\nОбновлено: {updated}\nВсего обработано: {total}"

    except zipfile.BadZipFile:
        return "Ошибка: Файл не является корректным ZIP архивом."
    except ValueError as e:
        return f"Ошибка данных: {e}"
    except Exception as e:
        app.logger.error(f"Неожиданная ошибка: {e}\n{traceback.format_exc()}")
        return f"Ошибка: {str(e)}"

# --- Telegram Webhook ---
@app.route(f"/{TELEGRAM_TOKEN}", methods=["POST"])
def webhook():
    """Обработчик вебхука Telegram."""
    if not request.is_json:
        app.logger.warning("Получен не JSON запрос")
        return "Bad Request", 400

    json_data = request.get_json()
    app.logger.debug(f"Получены данные: {json_data}")

    message = json_data.get("message")
    if not message:
        return "OK"

    chat_id = message.get("chat", {}).get("id")
    if not chat_id:
        app.logger.warning("Не удалось получить chat_id")
        return "OK"

    try:
        if "text" in message:
            text = message["text"]
            if text == "/start":
                # Логика /start с проверкой пользователя и магазина
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
                    # Регистрация нового пользователя
                    username = message.get("from", {}).get("username", "Неизвестный")
                    with app.app_context():
                        new_user = User(chat_id=str(chat_id), username=username)
                        try:
                            db.session.add(new_user)
                            db.session.commit()
                            app.logger.info(f"Новый пользователь {username} ({chat_id}) зарегистрирован.")
                            base_msg += "Вы зарегистрированы в базе"
                            if shop_exists: # Проверяем снова, вдруг магазин есть
                                 base_msg += " и у вас есть зарегистрированный магазин."
                            else:
                                 base_msg += ", но у вас нет зарегистрированных магазинов."
                        except Exception as e:
                            db.session.rollback()
                            app.logger.error(f"Ошибка регистрации пользователя {chat_id}: {e}")
                            base_msg += "Произошла ошибка при регистрации."

                send_message(chat_id, f"{base_msg}\n/words сканирование слов")

            elif text == "/words":
                send_message(chat_id, "Пожалуйста, отправьте ZIP файл с XLSX аналитики поиска.")

        elif "document" in message:
            doc = message["document"]
            if doc.get("file_name", "").lower().endswith('.zip'):
                try:
                    # Получение файла от Telegram
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

                    # Обработка
                    result_msg = process_zip_and_xlsx(file_content_res.content, doc["file_name"], chat_id)
                    send_message(chat_id, result_msg)

                except requests.exceptions.RequestException as e:
                    error_msg = f"Ошибка загрузки файла из Telegram: {e}"
                    app.logger.error(error_msg)
                    send_message(chat_id, error_msg)
                except Exception as e:
                    error_msg = f"Ошибка обработки файла: {e}"
                    app.logger.error(error_msg)
                    send_message(chat_id, error_msg)
            else:
                send_message(chat_id, "Пожалуйста, отправьте файл в формате ZIP.")

    except Exception as e:
        app.logger.error(f"Ошибка в webhook: {e}\n{traceback.format_exc()}")
        # Не отправляем сообщение об ошибке пользователю в webhook, чтобы не зациклить

    return "OK"

# --- Другие маршруты ---
@app.route("/")
def index():
    """Главная страница с информацией."""
    try:
        with app.app_context():
            inspector = inspect(db.engine)
            tables = inspector.get_table_names()

            counts = {}
            if 'users' in tables:
                counts['users'] = db.session.query(User).count()
            if 'shops' in tables:
                counts['shops'] = db.session.query(Shop).count()
            if 'phrases' in tables:
                counts['phrases'] = db.session.query(Phrase).count()

        html = "<h1>Бот Tittle работает!</h1><ul>"
        for table, count in counts.items():
            html += f"<li>{table.capitalize()}: {count}</li>"
        html += "</ul>"
        return html
    except Exception as e:
        app.logger.error(f"Ошибка на главной странице: {e}")
        return f"<h1>Ошибка</h1><p>{str(e)}</p>", 500

# --- Инициализация ---
if __name__ != "__main__":
    # Инициализация БД при запуске приложения (не при импорте)
    initialize_database()

if __name__ == "__main__":
    initialize_database() # Для локального запуска
    app.run(debug=True)
