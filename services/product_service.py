import requests
import time
import logging
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from models import db, WBProduct, SelectedProduct, WBApiKey

logger = logging.getLogger(__name__)

class ProductService:
    """Сервис для работы с товарами Wildberries"""
    
    BASE_URL = 'https://content-api.wildberries.ru'
    
    @staticmethod
    def get_products_from_wb(key_id: int) -> Tuple[bool, str, List[Dict]]:
        """Получение списка товаров из API Wildberries"""
        try:
            key = WBApiKey.query.get(key_id)
            if not key:
                return False, "Ключ не найден", []
            
            headers = {
                'Authorization': key.key,
                'Content-Type': 'application/json'
            }
            
            all_products = []
            cursor = None
            has_more = True
            
            while has_more:
                # Формируем запрос с пагинацией
                payload = {
                    "settings": {
                        "sort": {"ascending": True},
                        "cursor": {"limit": 100}
                    }
                }
                
                if cursor:
                    payload["settings"]["cursor"]["updatedAt"] = cursor.get('updatedAt')
                    payload["settings"]["cursor"]["nmID"] = cursor.get('nmID')
                
                # Добавляем небольшую задержку для соблюдения лимитов
                time.sleep(0.3)
                
                response = requests.post(
                    f'{ProductService.BASE_URL}/content/v2/get/cards/list',
                    headers=headers,
                    json=payload,
                    timeout=30
                )
                
                if response.status_code != 200:
                    error_data = response.json() if response.text else {}
                    return False, f"Ошибка API: {response.status_code}", []
                
                data = response.json()
                cards = data.get('cards', [])
                cursor = data.get('cursor', {})
                
                for card in cards:
                    # Извлекаем категорию из характеристик
                    category = ''
                    for char in card.get('characteristics', []):
                        if char.get('name', '').lower() == 'категория':
                            values = char.get('value', [])
                            if values and isinstance(values, list):
                                category = values[0] if values else ''
                            elif isinstance(values, str):
                                category = values
                            break
                    
                    all_products.append({
                        'nm_id': card.get('nmID'),
                        'vendor_code': card.get('vendorCode', ''),
                        'title': card.get('title', ''),
                        'brand': card.get('brand', ''),
                        'category': category,
                        'subject_id': card.get('subjectID'),
                        'subject_name': card.get('subjectName', ''),
                        'imt_id': card.get('imtID'),
                        'updated_at': card.get('updatedAt')
                    })
                
                # Проверяем, есть ли ещё данные
                has_more = cursor.get('total', 0) >= 100
                
            return True, f"Получено {len(all_products)} товаров", all_products
            
        except requests.exceptions.Timeout:
            return False, "Превышено время ожидания ответа от API", []
        except requests.exceptions.ConnectionError:
            return False, "Ошибка подключения к API", []
        except Exception as e:
            logger.error(f"Error getting products: {e}")
            return False, f"Ошибка: {str(e)}", []
    
    @staticmethod
    def update_products_db(key_id: int) -> Tuple[bool, str]:
        """Обновление товаров в базе данных"""
        try:
            success, message, products = ProductService.get_products_from_wb(key_id)
            if not success:
                return False, message
            
            if not products:
                return True, "Нет товаров для обновления"
            
            added = 0
            updated = 0
            
            for product_data in products:
                existing = WBProduct.query.filter_by(nm_id=product_data['nm_id']).first()
                
                if existing:
                    # Обновляем существующий товар
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
                    # Создаём новый товар
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
            return True, f"Добавлено: {added}, Обновлено: {updated}"
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error updating products: {e}")
            return False, f"Ошибка обновления БД: {str(e)}"
    
    @staticmethod
    def get_products_by_key(key_id: int, filters: Dict = None) -> List[WBProduct]:
        """Получение товаров по ключу с фильтрацией"""
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
    
    @staticmethod
    def toggle_select(product_id: int, key_id: int) -> Tuple[bool, str]:
        """Переключение статуса отметки товара"""
        try:
            # Проверяем, существует ли товар
            product = WBProduct.query.get(product_id)
            if not product:
                return False, "Товар не найден"
            
            # Проверяем, существует ли отметка
            selection = SelectedProduct.query.filter_by(
                product_id=product_id,
                key_id=key_id
            ).first()
            
            if selection:
                # Снимаем отметку
                db.session.delete(selection)
                db.session.commit()
                return True, "Отметка снята"
            else:
                # Добавляем отметку
                new_selection = SelectedProduct(
                    product_id=product_id,
                    key_id=key_id
                )
                db.session.add(new_selection)
                db.session.commit()
                return True, "Товар отмечен"
                
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error toggling selection: {e}")
            return False, f"Ошибка: {str(e)}"
    
    @staticmethod
    def get_selected_products(key_id: int) -> List[SelectedProduct]:
        """Получение отмеченных товаров для ключа"""
        return SelectedProduct.query.filter_by(key_id=key_id).all()