from flask import Flask, request
import requests
import os
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)

# Конфигурация базы данных
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///local.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Модель пользователя
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    chat_id = db.Column(db.String(50), unique=True)
    username = db.Column(db.String(100))

# Создание таблиц
with app.app_context():
    db.create_all()

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')

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
    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "")
    username = message.get("from", {}).get("username", "Неизвестный")
    
    if chat_id:
        # Сохраняем пользователя в БД
        user = User.query.filter_by(chat_id=str(chat_id)).first()
        if not user:
            user = User(chat_id=str(chat_id), username=username)
            db.session.add(user)
            db.session.commit()
        
        send_message(chat_id, f"Привет, @{username}! Вы написали: {text}")
    
    return "ok"

@app.route("/")
def index():
    users_count = User.query.count()
    return f"<h1>Бот работает!</h1><p>Пользователей в базе: {users_count}</p>"

if __name__ == "__main__":
    app.run(debug=True)