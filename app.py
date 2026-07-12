from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from models import db, WBApiKey
from services.key_manager import KeyManager
from services.wb_api import WBApiService
import os
import logging
import jwt
from datetime import datetime

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

# Настройка базы данных
database_url = os.environ.get('DATABASE_URL')
if database_url and database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)

app.config['SQLALCHEMY_DATABASE_URI'] = database_url or 'sqlite:///wb_keys.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

# Создание таблиц
with app.app_context():
    db.create_all()

# ==================== МАРШРУТЫ ====================

@app.route('/')
def index():
    """Главная страница с меню"""
    keys_count = WBApiKey.query.filter_by(is_active=True).count()
    return render_template('index.html', keys_count=keys_count)

@app.route('/keys')
def keys_list():
    """Управление ключами - список всех ключей"""
    keys = KeyManager.get_all_keys()
    return render_template('keys.html', keys=keys)

@app.route('/keys/add', methods=['GET', 'POST'])
def add_key():
    """Добавление нового ключа"""
    if request.method == 'POST':
        key = request.form.get('key', '').strip()
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()
        
        if not key or not name:
            flash('API ключ и название обязательны для заполнения', 'danger')
            return redirect(url_for('add_key'))
        
        success, message, new_key = KeyManager.add_key(key, name, description)
        flash(message, 'success' if success else 'danger')
        
        if success:
            return redirect(url_for('key_detail', key_id=new_key.id))
        else:
            return redirect(url_for('add_key'))
    
    return render_template('add_key.html')

@app.route('/keys/<int:key_id>')
def key_detail(key_id):
    """Детальная информация о ключе"""
    key = KeyManager.get_key(key_id)
    if not key:
        flash('Ключ не найден', 'danger')
        return redirect(url_for('keys_list'))
    
    full_info = KeyManager.get_key_full_info(key_id)
    return render_template('key_detail.html', info=full_info)

@app.route('/keys/<int:key_id>/check', methods=['POST'])
def check_key(key_id):
    """Проверка подключения по ключу"""
    success, message, details = KeyManager.check_key_connection(key_id)
    return jsonify({
        'success': success,
        'message': message,
        'details': details
    })

@app.route('/keys/<int:key_id>/delete', methods=['POST'])
def delete_key(key_id):
    """Удаление ключа"""
    success, message = KeyManager.delete_key(key_id)
    flash(message, 'success' if success else 'danger')
    return redirect(url_for('keys_list'))

@app.route('/keys/check-all', methods=['POST'])
def check_all_keys():
    """Проверка всех ключей"""
    results = KeyManager.check_all_keys()
    success_count = sum(1 for r in results.values() if r['success'])
    flash(f'Проверено {len(results)} ключей. Успешно: {success_count}, Ошибок: {len(results) - success_count}', 'info')
    return redirect(url_for('keys_list'))

@app.route('/advertising')
def advertising():
    """Управление рекламой"""
    return render_template('advertising.html')

@app.route('/products')
def products():
    """Управление товарами"""
    return render_template('products.html')

@app.route('/health')
def health():
    """Health check для Render"""
    return 'OK', 200

# ==================== ОБРАБОТЧИКИ ОШИБОК ====================

@app.errorhandler(404)
def not_found(error):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return render_template('500.html'), 500

# ==================== ЗАПУСК ====================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)