from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
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

# Настройка базы данных
database_url = os.environ.get('DATABASE_URL')
if database_url:
    if '?' in database_url:
        database_url += '&sslmode=require&connect_timeout=10&sslcompression=0'
    else:
        database_url += '?sslmode=require&connect_timeout=10&sslcompression=0'
    
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)

app.config['SQLALCHEMY_DATABASE_URI'] = database_url or 'sqlite:///wb_keys.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
    'pool_recycle': 280,
    'pool_timeout': 30,
    'max_overflow': 10,
    'pool_size': 5,
}

db.init_app(app)

# Создание таблиц
with app.app_context():
    db.create_all()

# Хранилище статусов фоновых задач
task_status = {}


# ==================== ФОНОВАЯ ЗАДАЧА С КОНТЕКСТОМ ====================

def run_update_products(key_id: int, task_id: str):
    """Фоновая задача для обновления товаров с контекстом приложения"""
    with app.app_context():
        try:
            task_status[task_id] = {'status': 'running', 'message': 'Начало обновления...', 'progress': 0}
            
            success, message, products = ProductService.get_products_from_wb(key_id)
            if not success:
                task_status[task_id] = {'status': 'error', 'message': message, 'progress': 0}
                return
            
            if not products:
                task_status[task_id] = {'status': 'completed', 'message': 'Нет товаров для обновления', 'progress': 100}
                return
            
            task_status[task_id] = {'status': 'running', 'message': f'Получено {len(products)} товаров, обновление БД...', 'progress': 30}
            
            added = 0
            updated = 0
            total = len(products)
            batch_size = 50
            
            for i in range(0, total, batch_size):
                batch = products[i:i+batch_size]
                
                try:
                    for product_data in batch:
                        existing = WBProduct.query.filter_by(nm_id=product_data['nm_id']).first()
                        
                        if existing:
                            existing.vendor_code = product_data.get('vendor_code', existing.vendor_code)
                            existing.title = product_data.get('title', existing.title)
                            existing.brand = product_data.get('brand', existing.brand)
                            existing.category = product_data.get('category', existing.category)
                            existing.subject_id = product_data.get('subject_id', existing.subject_id)
                            existing.subject_name = product_data.get('subject_name', existing.subject_name)
                            existing.imt_id = product_data.get('imt_id', existing.imt_id)
                            existing.updated_at = datetime.utcnow()
                            existing.key_id = key_id
                            updated += 1
                        else:
                            new_product = WBProduct(
                                nm_id=product_data['nm_id'],
                                vendor_code=product_data.get('vendor_code', ''),
                                title=product_data.get('title', ''),
                                brand=product_data.get('brand', ''),
                                category=product_data.get('category', ''),
                                subject_id=product_data.get('subject_id'),
                                subject_name=product_data.get('subject_name', ''),
                                imt_id=product_data.get('imt_id'),
                                key_id=key_id,
                                updated_at=datetime.utcnow()
                            )
                            db.session.add(new_product)
                            added += 1
                    
                    db.session.commit()
                    
                    progress = 30 + int(((i + len(batch)) / total) * 60)
                    task_status[task_id] = {
                        'status': 'running',
                        'message': f'Обработано {min(i + len(batch), total)}/{total} товаров...',
                        'progress': min(progress, 95)
                    }
                    
                except Exception as e:
                    db.session.rollback()
                    logger.error(f"Error processing batch: {e}")
                    continue
            
            task_status[task_id] = {
                'status': 'completed',
                'message': f'Добавлено: {added}, Обновлено: {updated}',
                'progress': 100,
                'added': added,
                'updated': updated,
                'total': total
            }
            logger.info(f"Products update completed for key {key_id}: added={added}, updated={updated}")
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error in background update: {e}")
            task_status[task_id] = {'status': 'error', 'message': str(e), 'progress': 0}


# ==================== ДЕКОРАТОР ====================

def db_retry(max_retries=3, delay=1):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return f(*args, **kwargs)
                except (OperationalError, DisconnectionError) as e:
                    error_str = str(e).lower()
                    if 'ssl syscall error' in error_str or 'eof detected' in error_str or 'connection' in error_str:
                        if attempt < max_retries - 1:
                            db.session.rollback()
                            logger.warning(f"DB error in {f.__name__}, retry {attempt+1}/{max_retries}: {e}")
                            time.sleep(delay * (attempt + 1))
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


# ==================== МАРШРУТЫ ====================

@app.route('/')
@db_retry()
def index():
    keys_count = WBApiKey.query.filter_by(is_active=True).count()
    return render_template('index.html', keys_count=keys_count)


@app.route('/keys')
@db_retry()
def keys_list():
    keys = KeyManager.get_all_keys(include_inactive=False)
    return render_template('keys.html', keys=keys, show_inactive=False)


@app.route('/keys/all')
@db_retry()
def keys_all():
    keys = KeyManager.get_all_keys(include_inactive=True)
    return render_template('keys.html', keys=keys, show_inactive=True)


@app.route('/keys/add', methods=['GET', 'POST'])
@db_retry()
def add_key():
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
@db_retry()
def key_detail(key_id):
    key = KeyManager.get_key(key_id)
    if not key:
        flash('Ключ не найден', 'danger')
        return redirect(url_for('keys_list'))
    
    full_info = KeyManager.get_key_full_info(key_id)
    return render_template('key_detail.html', info=full_info)


@app.route('/keys/<int:key_id>/check', methods=['POST'])
@db_retry()
def check_key(key_id):
    success, message, details = KeyManager.check_key_connection(key_id)
    return jsonify({'success': success, 'message': message, 'details': details})


@app.route('/keys/<int:key_id>/delete', methods=['POST'])
@db_retry()
def delete_key(key_id):
    success, message = KeyManager.delete_key_permanently(key_id)
    flash(message, 'success' if success else 'danger')
    referer = request.referrer or ''
    if 'keys/all' in referer:
        return redirect(url_for('keys_all'))
    return redirect(url_for('keys_list'))


@app.route('/keys/<int:key_id>/restore', methods=['POST'])
@db_retry()
def restore_key(key_id):
    success, message = KeyManager.restore_key(key_id)
    flash(message, 'success' if success else 'danger')
    return redirect(url_for('keys_all'))


@app.route('/keys/check-all', methods=['POST'])
@db_retry()
def check_all_keys():
    results = KeyManager.check_all_keys()
    success_count = sum(1 for r in results.values() if r['success'])
    flash(f'Проверено {len(results)} ключей. Успешно: {success_count}, Ошибок: {len(results) - success_count}', 'info')
    return redirect(url_for('keys_list'))


@app.route('/products')
@db_retry()
def products():
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
    
    filters = {
        'nm_id': request.args.get('nm_id', ''),
        'title': request.args.get('title', ''),
        'vendor_code': request.args.get('vendor_code', ''),
        'brand': request.args.get('brand', ''),
        'category': request.args.get('category', '')
    }
    
    products = ProductService.get_products_by_key(content_key.id, filters)
    selected_ids = [sel.product_id for sel in ProductService.get_selected_products(content_key.id)]
    
    last_update = None
    if products:
        last_update = max((p.updated_at for p in products if p.updated_at), default=None)
    
    task_id = request.args.get('task_id', '')
    task_info = task_status.get(task_id, {})
    
    return render_template('products.html', 
                         products=products, 
                         selected_ids=selected_ids,
                         filters=filters,
                         key_id=content_key.id,
                         last_update=last_update,
                         key_name=content_key.name,
                         task_id=task_id,
                         task_info=task_info)


@app.route('/products/update', methods=['POST'])
@db_retry()
def update_products():
    key_id = request.form.get('key_id', type=int)
    if not key_id:
        flash('Не указан ключ для обновления', 'danger')
        return redirect(url_for('products'))
    
    task_id = f"update_{key_id}_{int(time.time())}"
    
    # Запускаем обновление в фоновом потоке с контекстом приложения
    thread = threading.Thread(target=run_update_products, args=(key_id, task_id))
    thread.daemon = True
    thread.start()
    
    flash('Обновление товаров запущено в фоновом режиме. Это может занять несколько минут.', 'info')
    return redirect(url_for('products', task_id=task_id))


@app.route('/products/status')
@db_retry()
def products_status():
    task_id = request.args.get('task_id', '')
    if not task_id:
        return jsonify({'error': 'Не указан ID задачи'}), 400
    
    info = task_status.get(task_id, {'status': 'not_found', 'message': 'Задача не найдена'})
    return jsonify(info)


@app.route('/products/toggle/<int:product_id>', methods=['POST'])
@db_retry()
def toggle_product(product_id):
    key_id = request.form.get('key_id', type=int)
    if not key_id:
        return jsonify({'success': False, 'message': 'Не указан ключ'}), 400
    
    success, message = ProductService.toggle_select(product_id, key_id)
    return jsonify({'success': success, 'message': message})


@app.route('/products/selected')
@db_retry()
def selected_products():
    key_id = request.args.get('key_id', type=int)
    if not key_id:
        flash('Не указан ключ', 'danger')
        return redirect(url_for('products'))
    
    selections = ProductService.get_selected_products(key_id)
    products = [sel.product for sel in selections if sel.product]
    
    return render_template('selected_products.html', products=products, key_id=key_id)


@app.route('/advertising')
@db_retry()
def advertising():
    return render_template('advertising.html')


@app.route('/health')
def health():
    try:
        db.session.execute('SELECT 1')
        return 'OK', 200
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return 'DB Error', 500


@app.errorhandler(404)
def not_found(error):
    return render_template('404.html'), 404


@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    logger.error(f"Internal server error: {error}")
    return render_template('500.html'), 500


@app.errorhandler(OperationalError)
def handle_db_error(error):
    db.session.rollback()
    logger.error(f"Database error: {error}")
    flash('Ошибка подключения к базе данных. Попробуйте позже.', 'danger')
    return redirect(url_for('index'))


@app.errorhandler(502)
def handle_bad_gateway(error):
    logger.error(f"502 Bad Gateway: {error}")
    flash('Сервер временно недоступен. Попробуйте позже.', 'danger')
    return redirect(url_for('index'))


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)