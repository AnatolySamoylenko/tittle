from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from models import db, WBApiKey, WBProduct, SelectedProduct, WBApiLog
from services.key_manager import KeyManager
from services.wb_api import WBApiService
from services.product_service import ProductService
import os
import logging
import time
import threading
from datetime import datetime
from functools import wraps
from sqlalchemy.exc import OperationalError, DisconnectionError

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

# Пароль для доступа к сайту
SITE_PASSWORD = "Cjdtncbqcj.p"

# Настройка базы данных
database_url = os.environ.get('DATABASE_URL')
if database_url:
    # Добавляем параметры для стабильности соединения
    if '?' in database_url:
        database_url += '&sslmode=require&connect_timeout=10&keepalives_idle=60&keepalives_interval=10&keepalives_count=5&sslcompression=0'
    else:
        database_url += '?sslmode=require&connect_timeout=10&keepalives_idle=60&keepalives_interval=10&keepalives_count=5&sslcompression=0'
    
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)

app.config['SQLALCHEMY_DATABASE_URI'] = database_url or 'sqlite:///wb_keys.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Настройки пула соединений
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
    'pool_recycle': 280,
    'pool_timeout': 30,
    'max_overflow': 10,
    'pool_size': 5,
    'pool_reset_on_return': 'rollback',
    'echo_pool': False,
}

db.init_app(app)

# Создание таблиц
with app.app_context():
    db.create_all()

# Хранилище статусов фоновых задач
task_status = {}
task_progress = {}


# ==================== ДЕКОРАТОР ЗАЩИТЫ ПАРОЛЕМ ====================

def login_required(f):
    """Декоратор для проверки авторизации"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('authenticated'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


# ==================== МАРШРУТЫ АВТОРИЗАЦИИ ====================

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Страница входа с паролем"""
    # Если пользователь уже авторизован, перенаправляем на главную
    if session.get('authenticated'):
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        password = request.form.get('password', '')
        if password == SITE_PASSWORD:
            session['authenticated'] = True
            session.permanent = True
            flash('Доступ разрешён', 'success')
            return redirect(url_for('index'))
        else:
            flash('Неверный пароль. Попробуйте снова.', 'danger')
    
    return render_template('login.html')


@app.route('/logout')
def logout():
    """Выход из системы"""
    session.pop('authenticated', None)
    flash('Вы вышли из системы', 'info')
    return redirect(url_for('login'))


# ==================== ФОНОВАЯ ЗАДАЧА ====================

def run_update_products(key_id: int, task_id: str):
    """Фоновая задача для обновления товаров с прогрессом"""
    with app.app_context():
        try:
            task_status[task_id] = {'status': 'running', 'message': 'Начало обновления...', 'progress': 0}
            task_progress[task_id] = {'stage': 'start', 'progress': 0, 'message': 'Подготовка...'}
            
            def update_progress(stage, progress, message):
                task_progress[task_id] = {'stage': stage, 'progress': progress, 'message': message}
                task_status[task_id]['progress'] = progress
                task_status[task_id]['message'] = message
            
            success, message = ProductService.update_products_db(
                key_id, 
                batch_size=50,
                progress_callback=update_progress
            )
            
            if success:
                task_status[task_id] = {
                    'status': 'completed',
                    'message': message,
                    'progress': 100
                }
            else:
                task_status[task_id] = {
                    'status': 'error',
                    'message': message,
                    'progress': 0
                }
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error in background update: {e}")
            task_status[task_id] = {'status': 'error', 'message': str(e), 'progress': 0}


# ==================== ДЕКОРАТОР ДЛЯ ОБРАБОТКИ ОШИБОК БД ====================

def db_retry(max_retries=5, delay=1):
    """Декоратор для повторных попыток при ошибках БД"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return f(*args, **kwargs)
                except (OperationalError, DisconnectionError) as e:
                    error_str = str(e).lower()
                    if any(err in error_str for err in [
                        'ssl syscall error', 'eof detected', 'connection', 
                        'network', 'timeout', 'closed', 'reset'
                    ]):
                        if attempt < max_retries - 1:
                            db.session.rollback()
                            logger.warning(f"DB error in {f.__name__}, retry {attempt+1}/{max_retries}: {e}")
                            time.sleep(delay * (2 ** attempt))
                            continue
                        else:
                            logger.error(f"DB error in {f.__name__} after {max_retries} retries: {e}")
                            flash('Ошибка подключения к базе данных. Попробуйте позже.', 'danger')
                            return redirect(url_for('index'))
                    else:
                        raise
                except Exception as e:
                    logger.error(f"Unexpected error in {f.__name__}: {e}")
                    flash(f'Ошибка: {str(e)}', 'danger')
                    return redirect(url_for('index'))
            return redirect(url_for('index'))
        return decorated_function
    return decorator


# ==================== ГЛАВНАЯ СТРАНИЦА ====================

@app.route('/')
@login_required
@db_retry()
def index():
    """Главная страница с меню"""
    keys_count = WBApiKey.query.filter_by(is_active=True).count()
    return render_template('index.html', keys_count=keys_count)


# ==================== УПРАВЛЕНИЕ КЛЮЧАМИ ====================

@app.route('/keys')
@login_required
@db_retry()
def keys_list():
    """Управление ключами - список всех ключей (только активные)"""
    keys = KeyManager.get_all_keys(include_inactive=False)
    return render_template('keys.html', keys=keys, show_inactive=False)


@app.route('/keys/all')
@login_required
@db_retry()
def keys_all():
    """Управление ключами - список всех ключей (включая неактивные)"""
    keys = KeyManager.get_all_keys(include_inactive=True)
    return render_template('keys.html', keys=keys, show_inactive=True)


@app.route('/keys/add', methods=['GET', 'POST'])
@login_required
@db_retry()
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
@login_required
@db_retry()
def key_detail(key_id):
    """Детальная информация о ключе"""
    key = KeyManager.get_key(key_id)
    if not key:
        flash('Ключ не найден', 'danger')
        return redirect(url_for('keys_list'))
    
    full_info = KeyManager.get_key_full_info(key_id)
    return render_template('key_detail.html', info=full_info)


@app.route('/keys/<int:key_id>/check', methods=['POST'])
@login_required
@db_retry()
def check_key(key_id):
    """Проверка подключения по ключу"""
    success, message, details = KeyManager.check_key_connection(key_id)
    return jsonify({
        'success': success,
        'message': message,
        'details': details
    })


@app.route('/keys/<int:key_id>/delete', methods=['POST'])
@login_required
@db_retry()
def delete_key(key_id):
    """Полное удаление ключа из базы данных"""
    success, message = KeyManager.delete_key_permanently(key_id)
    flash(message, 'success' if success else 'danger')
    referer = request.referrer or ''
    if 'keys/all' in referer:
        return redirect(url_for('keys_all'))
    return redirect(url_for('keys_list'))


@app.route('/keys/<int:key_id>/restore', methods=['POST'])
@login_required
@db_retry()
def restore_key(key_id):
    """Восстановление удалённого (неактивного) ключа"""
    success, message = KeyManager.restore_key(key_id)
    flash(message, 'success' if success else 'danger')
    return redirect(url_for('keys_all'))


@app.route('/keys/check-all', methods=['POST'])
@login_required
@db_retry()
def check_all_keys():
    """Проверка всех активных ключей"""
    results = KeyManager.check_all_keys()
    success_count = sum(1 for r in results.values() if r['success'])
    flash(f'Проверено {len(results)} ключей. Успешно: {success_count}, Ошибок: {len(results) - success_count}', 'info')
    return redirect(url_for('keys_list'))


# ==================== УПРАВЛЕНИЕ ТОВАРАМИ ====================

@app.route('/products')
@login_required
@db_retry()
def products():
    """Управление товарами"""
    keys = KeyManager.get_all_keys(include_inactive=False)
    if not keys:
        flash('Необходимо добавить API ключ для работы с товарами', 'warning')
        return redirect(url_for('keys_list'))
    
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
    
    # Фильтры
    filters = {
        'nm_id': request.args.get('nm_id', ''),
        'title': request.args.get('title', ''),
        'vendor_code': request.args.get('vendor_code', ''),
        'brand': request.args.get('brand', ''),
        'subject_name': request.args.get('subject_name', '')
    }
    
    products = ProductService.get_products_by_key(content_key.id, filters)
    
    # Получаем список ID отмеченных товаров для этого ключа
    selected_ids = []
    for product in products:
        if product.is_selected:
            selected_ids.append(product.id)
    
    last_update = None
    if products:
        last_update = max((p.updated_at for p in products if p.updated_at), default=None)
    
    task_id = request.args.get('task_id', '')
    task_info = task_status.get(task_id, {})
    progress_info = task_progress.get(task_id, {})
    
    # Если задача завершена и есть task_id, перезагружаем без task_id
    if task_info.get('status') == 'completed' and task_id:
        return redirect(url_for('products'))
    
    return render_template('products.html', 
                         products=products, 
                         selected_ids=selected_ids,
                         filters=filters,
                         key_id=content_key.id,
                         last_update=last_update,
                         key_name=content_key.name,
                         task_id=task_id,
                         task_info=task_info,
                         progress_info=progress_info)


@app.route('/products/update', methods=['POST'])
@login_required
@db_retry()
def update_products():
    """Запуск обновления списка товаров в фоновом режиме"""
    key_id = request.form.get('key_id', type=int)
    if not key_id:
        flash('Не указан ключ для обновления', 'danger')
        return redirect(url_for('products'))
    
    task_id = f"update_{key_id}_{int(time.time())}"
    
    thread = threading.Thread(target=run_update_products, args=(key_id, task_id))
    thread.daemon = True
    thread.start()
    
    flash('Обновление товаров запущено в фоновом режиме. Это может занять несколько минут.', 'info')
    return redirect(url_for('products', task_id=task_id))


@app.route('/products/status')
@login_required
@db_retry()
def products_status():
    """Проверка статуса обновления товаров"""
    task_id = request.args.get('task_id', '')
    if not task_id:
        return jsonify({'error': 'Не указан ID задачи'}), 400
    
    info = task_status.get(task_id, {'status': 'not_found', 'message': 'Задача не найдена'})
    progress = task_progress.get(task_id, {})
    
    return jsonify({
        **info,
        'progress_detail': progress
    })


@app.route('/products/toggle/<int:product_id>', methods=['POST'])
@login_required
@db_retry()
def toggle_product(product_id):
    """Переключение отметки товара"""
    key_id = request.form.get('key_id', type=int)
    if not key_id:
        return jsonify({'success': False, 'message': 'Не указан ключ'}), 400
    
    success, message = ProductService.toggle_select(product_id, key_id)
    return jsonify({'success': success, 'message': message})


@app.route('/products/selected')
@login_required
@db_retry()
def selected_products():
    """Список отмеченных товаров"""
    key_id = request.args.get('key_id', type=int)
    if not key_id:
        flash('Не указан ключ', 'danger')
        return redirect(url_for('products'))
    
    # Получаем отмеченные товары из отдельной таблицы
    selected_items = SelectedProduct.query.filter_by(key_id=key_id).all()
    selected_nm_ids = [item.nm_id for item in selected_items]
    
    # Получаем полную информацию о товарах
    products = WBProduct.query.filter(WBProduct.nm_id.in_(selected_nm_ids)).all()
    
    return render_template('selected_products.html', 
                         products=products,
                         key_id=key_id)


# ==================== УПРАВЛЕНИЕ РЕКЛАМОЙ ====================

@app.route('/advertising')
@login_required
@db_retry()
def advertising():
    """Управление рекламой"""
    return render_template('advertising.html')


# ==================== HEALTH CHECK ====================

@app.route('/health')
def health():
    """Health check для Render (без проверки пароля)"""
    try:
        db.session.execute('SELECT 1')
        return 'OK', 200
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        try:
            db.session.rollback()
            db.session.execute('SELECT 1')
            return 'OK (recovered)', 200
        except:
            return 'DB Error', 500


# ==================== ОБРАБОТЧИКИ ОШИБОК ====================

@app.errorhandler(404)
def not_found(error):
    """Страница не найдена"""
    if not session.get('authenticated'):
        return redirect(url_for('login'))
    return render_template('404.html'), 404


@app.errorhandler(500)
def internal_error(error):
    """Внутренняя ошибка сервера"""
    db.session.rollback()
    logger.error(f"Internal server error: {error}")
    if not session.get('authenticated'):
        return redirect(url_for('login'))
    return render_template('500.html'), 500


@app.errorhandler(OperationalError)
def handle_db_error(error):
    """Обработчик ошибок базы данных с попыткой восстановления"""
    db.session.rollback()
    logger.error(f"Database error: {error}")
    
    if not session.get('authenticated'):
        return redirect(url_for('login'))
    
    try:
        db.session.execute('SELECT 1')
        flash('Соединение с базой данных восстановлено', 'success')
        return redirect(request.referrer or url_for('index'))
    except:
        flash('Ошибка подключения к базе данных. Попробуйте позже.', 'danger')
        return redirect(url_for('index'))


@app.errorhandler(502)
def handle_bad_gateway(error):
    """Обработчик ошибки 502 Bad Gateway"""
    logger.error(f"502 Bad Gateway: {error}")
    if not session.get('authenticated'):
        return redirect(url_for('login'))
    flash('Сервер временно недоступен. Попробуйте позже.', 'danger')
    return redirect(url_for('index'))


# ==================== ЗАПУСК ====================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)