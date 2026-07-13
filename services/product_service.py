import requests
import time
import logging
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from models import db, WBProduct, SelectedProduct, WBApiKey
from sqlalchemy.exc import OperationalError, DisconnectionError
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

class ProductService:
    """Сервис для работы с товарами Wildberries"""
    
    BASE_URL = 'https://content-api.wildberries.ru'
    
    @staticmethod
    def _execute_with_retry(func, retries=5, delay=1):
        """Выполняет функцию с повторными попытками при ошибках соединения с БД"""
        for attempt in range(retries):
            try:
                return func()
            except (OperationalError, DisconnectionError) as e:
                error_str = str(e).lower()
                if any(err in error_str for err in [
                    'ssl syscall error', 'eof detected', 'connection', 
                    'network', 'timeout', 'closed', 'reset'
                ]):
                    logger.warning(f"Database connection error (attempt {attempt+1}/{retries}): {e}")
                    if attempt < retries - 1:
                        db.session.rollback()
                        time.sleep(delay * (2 ** attempt))
                        continue
                    else:
                        logger.error(f"Database connection error after {retries} attempts: {e}")
                        raise
                else:
                    raise
            except Exception as e:
                logger.error(f"Unexpected error in _execute_with_retry: {e}")
                raise
    
    @staticmethod
    def get_products_from_wb(key_id: int, progress_callback=None) -> Tuple[bool, str, List[Dict]]:
        """Получение списка товаров из API Wildberries с таймаутами и прогрессом"""
        try:
            def get_key():
                return WBApiKey.query.get(key_id)
            
            key = ProductService._execute_with_retry(get_key)
            if not key:
                return False, "Ключ не найден", []
            
            headers = {
                'Authorization': key.key,
                'Content-Type': 'application/json'
            }
            
            session = requests.Session()
            retries = Retry(
                total=3,
                backoff_factor=1,
                status_forcelist=[408, 429, 500, 502, 503, 504],
                allowed_methods=["POST"],
                raise_on_status=False
            )
            adapter = HTTPAdapter(
                max_retries=retries,
                pool_connections=10,
                pool_maxsize=10
            )
            session.mount('http://', adapter)
            session.mount('https://', adapter)
            
            all_products = []
            cursor = None
            has_more = True
            page = 0
            request_timeout = (15, 120)
            
            if progress_callback:
                progress_callback('start', 0, 'Начало получения товаров...')
            
            while has_more:
                page += 1
                payload = {
                    "settings": {
                        "sort": {"ascending": True},
                        "cursor": {"limit": 100}
                    }
                }
                
                if cursor:
                    payload["settings"]["cursor"]["updatedAt"] = cursor.get('updatedAt')
                    payload["settings"]["cursor"]["nmID"] = cursor.get('nmID')
                
                time.sleep(0.3)
                
                try:
                    if progress_callback:
                        progress_callback('fetching', page, f'Загрузка страницы {page}...')
                    
                    response = session.post(
                        f'{ProductService.BASE_URL}/content/v2/get/cards/list',
                        headers=headers,
                        json=payload,
                        timeout=request_timeout
                    )
                    
                    if response.status_code == 429:
                        retry_after = int(response.headers.get('Retry-After', 5))
                        logger.warning(f"Rate limit exceeded, waiting {retry_after} seconds")
                        if progress_callback:
                            progress_callback('waiting', page, f'Лимит запросов, ожидание {retry_after} сек...')
                        time.sleep(retry_after)
                        continue
                    
                    if response.status_code != 200:
                        error_data = response.json() if response.text else {}
                        error_msg = error_data.get('errorText', f"Ошибка API: {response.status_code}")
                        return False, error_msg, []
                    
                    data = response.json()
                    cards = data.get('cards', [])
                    cursor = data.get('cursor', {})
                    
                    # Логируем первый товар для отладки категории
                    if page == 1 and cards:
                        logger.info(f"Sample product data: {cards[0].get('characteristics', [])}")
                    
                    for card in cards:
                        category = ''
                        subject_name = card.get('subjectName', '')
                        
                        # Ищем категорию в характеристиках
                        for char in card.get('characteristics', []):
                            char_name = char.get('name', '').lower()
                            # Проверяем разные варианты названия категории
                            if char_name in ['категория', 'category', 'категория товара', 'категория товаров', 'категория товара:']:
                                values = char.get('value', [])
                                if values:
                                    if isinstance(values, list) and values:
                                        # Берём первое значение из списка
                                        category = values[0] if values else ''
                                    elif isinstance(values, str):
                                        category = values
                                    break
                        
                        # Если категория не найдена, пробуем другие поля
                        if not category:
                            # Пробуем взять из subjectName
                            category = subject_name
                        
                        # Если всё ещё пусто, пробуем взять из parentName (если есть)
                        if not category:
                            for char in card.get('characteristics', []):
                                if 'родительская' in char.get('name', '').lower():
                                    values = char.get('value', [])
                                    if values and isinstance(values, list) and values:
                                        category = values[0]
                                        break
                        
                        all_products.append({
                            'nm_id': card.get('nmID'),
                            'vendor_code': card.get('vendorCode', ''),
                            'title': card.get('title', ''),
                            'brand': card.get('brand', ''),
                            'category': category,
                            'subject_id': card.get('subjectID'),
                            'subject_name': subject_name,
                            'imt_id': card.get('imtID'),
                            'updated_at': card.get('updatedAt')
                        })
                    
                    has_more = cursor.get('total', 0) >= 100
                    
                    if progress_callback:
                        progress_callback('page_complete', page, f'Загружено {len(all_products)} товаров')
                    
                except requests.exceptions.Timeout:
                    logger.error(f"Timeout getting products for key {key_id}")
                    return False, "Превышено время ожидания ответа от API Wildberries. Попробуйте позже.", []
                except requests.exceptions.ConnectionError:
                    logger.error(f"Connection error getting products for key {key_id}")
                    return False, "Ошибка подключения к API Wildberries. Проверьте интернет-соединение.", []
                except Exception as e:
                    logger.error(f"Error in get_products_from_wb: {e}")
                    return False, f"Ошибка при получении товаров: {str(e)}", []
            
            if progress_callback:
                progress_callback('complete', page, f'Получено {len(all_products)} товаров')
            
            return True, f"Получено {len(all_products)} товаров", all_products
            
        except Exception as e:
            logger.error(f"Unexpected error in get_products_from_wb: {e}")
            return False, f"Неожиданная ошибка: {str(e)}", []
    
    @staticmethod
    def update_products_db(key_id: int, batch_size: int = 50, progress_callback=None) -> Tuple[bool, str]:
        """Обновление товаров в базе данных с обработкой по частям и прогрессом"""
        try:
            if progress_callback:
                progress_callback('start', 0, 'Начало обновления...')
            
            # Получаем товары из API
            success, message, products = ProductService.get_products_from_wb(
                key_id, 
                progress_callback=lambda stage, page, msg: progress_callback('api', page, msg) if progress_callback else None
            )
            if not success:
                return False, message
            
            if not products:
                return True, "Нет товаров для обновления"
            
            added = 0
            updated = 0
            total = len(products)
            
            if progress_callback:
                progress_callback('db_start', 0, f'Обновление БД: {total} товаров...')
            
            # Обрабатываем товары по частям
            for i in range(0, total, batch_size):
                batch = products[i:i+batch_size]
                batch_start = i + 1
                batch_end = min(i + batch_size, total)
                
                if progress_callback:
                    progress_callback('db_batch', int((i / total) * 100), 
                                    f'Обработка {batch_start}-{batch_end} из {total}...')
                
                def update_batch():
                    nonlocal added, updated
                    for product_data in batch:
                        try:
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
                        except Exception as e:
                            logger.error(f"Error processing product {product_data.get('nm_id')}: {e}")
                            continue
                    
                    db.session.commit()
                
                ProductService._execute_with_retry(update_batch)
                logger.info(f"Processed batch {batch_start}-{batch_end} of {total}")
            
            if progress_callback:
                progress_callback('complete', 100, f'Готово! Добавлено: {added}, Обновлено: {updated}')
            
            return True, f"Добавлено: {added}, Обновлено: {updated}"
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error updating products: {e}")
            if progress_callback:
                progress_callback('error', 0, f'Ошибка: {str(e)}')
            return False, f"Ошибка обновления БД: {str(e)}"
    
    @staticmethod
    def get_products_by_key(key_id: int, filters: Dict = None) -> List[WBProduct]:
        """Получение товаров по ключу с фильтрацией"""
        try:
            def query_products():
                query = WBProduct.query.filter_by(key_id=key_id)
                
                if filters:
                    if filters.get('nm_id'):
                        query = query.filter(WBProduct.nm_id.ilike(f"%{filters['nm_id']}%"))
                    if filters.get('title'):
                        query = query.filter(WBProduct.title.ilike(f"%{filters['title']}%"))
                    if filters.get('vendor_code'):
                        query = query.filter(WBProduct.vendor_code.ilike(f"%{filters['vendor_code']}%"))
                    if filters.get('brand'):
                        query = query.filter(WBProduct.brand.ilike(f"%{filters['brand']}%"))
                    if filters.get('category'):
                        query = query.filter(WBProduct.category.ilike(f"%{filters['category']}%"))
                
                return query.order_by(WBProduct.nm_id).all()
            
            return ProductService._execute_with_retry(query_products)
            
        except Exception as e:
            logger.error(f"Error getting products by key: {e}")
            return []
    
    @staticmethod
    def toggle_select(product_id: int, key_id: int) -> Tuple[bool, str]:
        """Переключение статуса отметки товара"""
        try:
            def toggle():
                product = WBProduct.query.get(product_id)
                if not product:
                    return False, "Товар не найден"
                
                selection = SelectedProduct.query.filter_by(
                    product_id=product_id,
                    key_id=key_id
                ).first()
                
                if selection:
                    db.session.delete(selection)
                    db.session.commit()
                    return True, "Отметка снята"
                else:
                    new_selection = SelectedProduct(
                        product_id=product_id,
                        key_id=key_id
                    )
                    db.session.add(new_selection)
                    db.session.commit()
                    return True, "Товар отмечен"
            
            return ProductService._execute_with_retry(toggle)
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error toggling selection: {e}")
            return False, f"Ошибка: {str(e)}"
    
    @staticmethod
    def get_selected_products(key_id: int) -> List[SelectedProduct]:
        """Получение отмеченных товаров для ключа"""
        try:
            def query_selected():
                return SelectedProduct.query.filter_by(key_id=key_id).all()
            
            return ProductService._execute_with_retry(query_selected)
            
        except Exception as e:
            logger.error(f"Error getting selected products: {e}")
            return []