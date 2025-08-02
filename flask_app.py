# flask_app.py
from flask import Flask, request
import requests
import os
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect

app = Flask(__name__)

# Конфигурация базы данных SQLite
db_path = '/home/AnatolySamoylenko/tittle_database.db'
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Модель пользователя
class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    chat_id = db.Column(db.String(50), unique=True)
    username = db.Column(db.String(100))

# Модель магазина
class Shop(db.Model):
    __tablename__ = 'shops'
    shopId = db.Column(db.Integer, primary_key=True, autoincrement=False)
    API = db.Column(db.Text, nullable=False)
    chatId = db.Column(db.Integer, nullable=False)

# Функция для проверки и инициализации базы данных
def initialize_database():
    with app.app_context():
        inspector = inspect(db.engine)
        existing_tables = inspector.get_table_names()

        if 'shops' not in existing_tables:
            print("Таблица 'shops' не найдена. Создаём...")
            try:
                db.create_all()
                print("Таблица 'shops' успешно создана.")
            except Exception as e:
                print(f"Ошибка при создании таблицы 'shops': {e}")
        else:
            print("Таблица 'shops' уже существует.")

        if 'users' not in existing_tables:
            print("Таблица 'users' не найдена. Создаём...")
            try:
                db.create_all()
                print("Таблица 'users' успешно создана.")
            except Exception as e:
                print(f"Ошибка при создании таблицы 'users': {e}")

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', 'ваш_токен_здесь_для_теста')

def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text
    }
    requests.post(url, json=payload)

# Функция для проверки наличия записи в shops по chatId
def check_shop_exists_for_chat(chat_id):
    """Проверяет, есть ли запись в таблице shops для данного chat_id"""
    try:
        with app.app_context():
            # Проверяем, существует ли таблица shops
            inspector = inspect(db.engine)
            if 'shops' not in inspector.get_table_names():
                print("Таблица 'shops' не существует.")
                return False
            
            # Проверяем наличие записи
            shop_exists = db.session.query(Shop).filter_by(chatId=chat_id).first() is not None
            return shop_exists
    except Exception as e:
        print(f"Ошибка при проверке наличия магазина: {e}")
        return False

@app.route(f"/{TELEGRAM_TOKEN}", methods=["POST"])
def webhook():
    data = request.get_json()
    
    if "message" in 
        message = data["message"]
        chat_id = message["chat"]["id"]
        text = message.get("text", "")
        username = message.get("from", {}).get("username", "Неизвестный")
        
        # Проверяем наличие записи в shops для этого chat_id
        shop_exists = check_shop_exists_for_chat(chat_id)
        
        # Если записи нет - уведомляем пользователя
        if not shop_exists:
            send_message(chat_id, "У вас нет зарегистрированных магазинов.")
        
        if text == "/start":
            if shop_exists:
                send_message(chat_id, "Привет, это я бот! У вас есть зарегистрированный магазин.")
            else:
                send_message(chat_id, "Привет, это я бот! У вас пока нет зарегистрированных магазинов.")
            
            # Сохраняем нового пользователя в БД
            with app.app_context():
                user = User.query.filter_by(chat_id=str(chat_id)).first()
                if not user:
                    user = User(chat_id=str(chat_id), username=username)
                    try:
                        db.session.add(user)
                        db.session.commit()
                        print(f"Пользователь {username} ({chat_id}) добавлен в БД.")
                    except Exception as e:
                        db.session.rollback()
                        print(f"Ошибка при добавлении пользователя: {e}")
        
        elif text:
            if shop_exists:
                send_message(chat_id, f"Привет, @{username}! Вы написали: {text}")
            # Если магазина нет, сообщение уже отправлено выше
    
    return "ok"

@app.route("/")
def index():
    try:
        with app.app_context():
            users_count = User.query.count()
            inspector = inspect(db.engine)
            if 'shops' in inspector.get_table_names():
                shops_count = Shop.query.count()
            else:
                shops_count = 0
        return f"<h1>Бот работает!</h1><p>Пользователей в базе: {users_count}</p><p>Магазинов в базе: {shops_count}</p>"
    except Exception as e:
        return f"<h1>Ошибка</h1><p>{str(e)}</p>", 500

# Инициализация базы данных
initialize_database()

if __name__ == "__main__":
    app.run(debug=True)
