from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from models import db, WBApiKey, WBProduct, SelectedProduct, WBApiLog
from services.key_manager import KeyManager
from services.wb_api import WBApiService
from services.product_service import ProductService
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


# ==================== ГЛАВНАЯ СТРАНИЦА ====================

@app.route('/')
def index():
    """Главная страница с меню"""
    keys_count = WBApiKey.query.filter_by(is_active=True).count()
    return render_template('index.html', keys_count=keys_count)


# ==================== УПРАВЛЕНИЕ КЛЮЧАМИ ====================

@app.route('/keys')
def keys_list():
    """Управление ключами - список всех ключей (только активные)"""
    keys = KeyManager.get_all_keys(include_inactive=False)
    return render_template('keys.html', keys=keys, show_inactive=False)


@app.route('/keys/all')
def keys_all():
    """Управление ключами - список всех ключей (включая неактивные)"""
    keys = KeyManager.get_all_keys(include_inactive=True)
    return render_template('keys.html', keys=keys, show_inactive=True)


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
    """Полное удаление ключа из базы данных"""
    success, message = KeyManager.delete_key_permanently(key_id)
    flash(message, 'success' if success else 'danger')
    # Определяем, откуда пришли (из списка всех ключей или активных)
    referer = request.referrer or ''
    if 'keys/all' in referer:
        return redirect(url_for('keys_all'))
    return redirect(url_for('keys_list'))


@app.route('/keys/<int:key_id>/restore', methods=['POST'])
def restore_key(key_id):
    """Восстановление удалённого (неактивного) ключа"""
    success, message = KeyManager.restore_key(key_id)
    flash(message, 'success' if success else 'danger')
    return redirect(url_for('keys_all'))


@app.route('/keys/check-all', methods=['POST'])
def check_all_keys():
    """Проверка всех активных ключей"""
    results = KeyManager.check_all_keys()
    success_count = sum(1 for r in results.values() if r['success'])
    flash(f'Проверено {len(results)} ключей. Успешно: {success_count}, Ошибок: {len(results) - success_count}', 'info')
    return redirect(url_for('keys_list'))


# ==================== УПРАВЛЕНИЕ ТОВАРАМИ ====================

@app.route('/products')
def products():
    """Управление товарами"""
    # Проверяем, есть ли у пользователя активный ключ с доступом к Контенту
    keys = KeyManager.get_all_keys(include_inactive=False)
    if not keys:
        flash('Необходимо добавить API ключ для работы с товарами', 'warning')
        return redirect(url_for('keys_list'))
    
    # Проверяем, есть ли ключ с доступом к Контенту
    has_content_access = False
    content_key = None
    for key in keys:
        access = key.access_info.get('access_categories', {})
        if access.get('content', False) and key.is_active:
            has_content_access = True
            content_key = key
            break
    
    if not has_content_access:
        flash('Для доступа к управлению товарами необходим ключ с доступом к разделу "Контент"', 'danger')
        return redirect(url_for('index'))
    
    # Получаем товары с фильтрацией
    filters = {
        'nm_id': request.args.get('nm_id', ''),
        'title': request.args.get('title', ''),
        'vendor_code': request.args.get('vendor_code', ''),
        'brand': request.args.get('brand', ''),
        'category': request.args.get('category', '')
    }
    
    products = ProductService.get_products_by_key(content_key.id, filters)
    
    # Получаем список отмеченных товаров для этого ключа
    selected_ids = [sel.product_id for sel in ProductService.get_selected_products(content_key.id)]
    
    # Получаем информацию о последнем обновлении
    last_update = None
    if products:
        last_update = max((p.updated_at for p in products if p.updated_at), default=None)
    
    return render_template('products.html', 
                         products=products, 
                         selected_ids=selected_ids,
                         filters=filters,
                         key_id=content_key.id,
                         last_update=last_update,
                         key_name=content_key.name)


@app.route('/products/update', methods=['POST'])
def update_products():
    """Обновление списка товаров"""
    key_id = request.form.get('key_id', type=int)
    if not key_id:
        flash('Не указан ключ для обновления', 'danger')
        return redirect(url_for('products'))
    
    success, message = ProductService.update_products_db(key_id)
    flash(message, 'success' if success else 'danger')
    return redirect(url_for('products'))


@app.route('/products/toggle/<int:product_id>', methods=['POST'])
def toggle_product(product_id):
    """Переключение отметки товара"""
    key_id = request.form.get('key_id', type=int)
    if not key_id:
        return jsonify({'success': False, 'message': 'Не указан ключ'}), 400
    
    success, message = ProductService.toggle_select(product_id, key_id)
    return jsonify({'success': success, 'message': message})


@app.route('/products/selected')
def selected_products():
    """Список отмеченных товаров"""
    key_id = request.args.get('key_id', type=int)
    if not key_id:
        flash('Не указан ключ', 'danger')
        return redirect(url_for('products'))
    
    selections = ProductService.get_selected_products(key_id)
    products = [sel.product for sel in selections if sel.product]
    
    return render_template('selected_products.html', 
                         products=products,
                         key_id=key_id)


# ==================== УПРАВЛЕНИЕ РЕКЛАМОЙ ====================

@app.route('/advertising')
def advertising():
    """Управление рекламой"""
    return render_template('advertising.html')


# ==================== HEALTH CHECK ====================

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