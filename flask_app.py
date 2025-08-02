from flask import Flask, request
import requests
import os
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)

# Конфигурация базы данных SQLite
db_path = '/home/AnatolySamoylenko/tittle_database.db'
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Модель пользователя
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    chat_id = db.Column(db.String(50), unique=True)
    username = db.Column(db.String(100))

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', 'ваш_токен_здесь_для_теста')

def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text
    }
    requests.post(url, json=payload)

@app.route(f"/{TELEGRAM_TOKEN}", methods=["POST"])
def webhook():
    data = request.get_json()
    
    # Проверяем, является ли сообщение командой
    if "message" in data:
        message = data["message"]
        chat_id = message["chat"]["id"]
        text = message.get("text", "")
        username = message.get("from", {}).get("username", "Неизвестный")
        
        # Обработка команды /start
        if text == "/start":
            send_message(chat_id, "Привет, это я бот!")
            
            # Сохраняем нового пользователя в БД
            with app.app_context():
                user = User.query.filter_by(chat_id=str(chat_id)).first()
                if not user:
                    user = User(chat_id=str(chat_id), username=username)
                    db.session.add(user)
                    db.session.commit()
        
        # Обработка обычных сообщений
        elif text:
            with app.app_context():
                user = User.query.filter_by(chat_id=str(chat_id)).first()
                if not user:
                    user = User(chat_id=str(chat_id), username=username)
                    db.session.add(user)
                    db.session.commit()
            
            send_message(chat_id, f"Привет, @{username}! Вы написали: {text}")
    
    return "ok"

@app.route("/")
def index():
    with app.app_context():
        users_count = User.query.count()
    return f"<h1>Бот работает!</h1><p>Пользователей в базе: {users_count}</p>"

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)