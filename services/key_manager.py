from models import db, WBApiKey, WBApiLog, WBProduct, SelectedProduct
from services.wb_api import WBApiService
from datetime import datetime
import logging
import time
from typing import Dict, List, Optional, Tuple
from sqlalchemy.exc import OperationalError, DisconnectionError

logger = logging.getLogger(__name__)

class KeyManager:
    """Сервис для управления API ключами"""
    
    @staticmethod
    def _execute_with_retry(func, retries=3, delay=1):
        """Выполняет функцию с повторными попытками при ошибках соединения с БД"""
        for attempt in range(retries):
            try:
                return func()
            except (OperationalError, DisconnectionError) as e:
                error_str = str(e).lower()
                if 'ssl syscall error' in error_str or 'eof detected' in error_str or 'connection' in error_str:
                    logger.warning(f"Database connection error (attempt {attempt+1}/{retries}): {e}")
                    if attempt < retries - 1:
                        db.session.rollback()
                        time.sleep(delay * (attempt + 1))
                        continue
                    else:
                        raise
                else:
                    raise
            except Exception as e:
                logger.error(f"Unexpected error in _execute_with_retry: {e}")
                raise
    
    @staticmethod
    def add_key(key: str, name: str, description: str = '') -> Tuple[bool, str, Optional[WBApiKey]]:
        """Добавление нового ключа"""
        try:
            def check_existing():
                return WBApiKey.query.filter_by(key=key).first()
            
            existing = KeyManager._execute_with_retry(check_existing)
            if existing:
                if existing.is_active:
                    return False, f"Ключ с таким значением уже существует (ID: {existing.id}, название: '{existing.name}')", None
                else:
                    return False, f"Ключ с таким значением существует, но помечен как неактивный (ID: {existing.id}). Используйте восстановление или удалите его.", None
            
            # Получаем информацию о ключе
            wb_api = WBApiService(key)
            token_info = wb_api.get_token_info()
            
            new_key = WBApiKey(
                key=key,
                name=name,
                description=description,
                token_type=token_info.get('token_type', 'unknown'),
                access_info=token_info,
                is_active=True
            )
            
            def save_key():
                db.session.add(new_key)
                db.session.commit()
            
            KeyManager._execute_with_retry(save_key)
            
            # Проверяем подключение
            KeyManager.check_key_connection(new_key.id)
            
            return True, "Ключ успешно добавлен", new_key
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error adding key: {e}")
            return False, f"Ошибка при добавлении ключа: {str(e)}", None
    
    @staticmethod
    def get_all_keys(include_inactive: bool = False) -> List[WBApiKey]:
        """Получение всех ключей с обработкой ошибок БД"""
        try:
            def query_keys():
                if include_inactive:
                    return WBApiKey.query.order_by(WBApiKey.created_at.desc()).all()
                return WBApiKey.query.filter_by(is_active=True).order_by(WBApiKey.created_at.desc()).all()
            
            return KeyManager._execute_with_retry(query_keys)
        except Exception as e:
            logger.error(f"Error getting all keys: {e}")
            return []
    
    @staticmethod
    def get_key(key_id: int) -> Optional[WBApiKey]:
        """Получение ключа по ID (только активные) с обработкой ошибок БД"""
        try:
            def query_key():
                return WBApiKey.query.filter_by(id=key_id, is_active=True).first()
            
            return KeyManager._execute_with_retry(query_key)
        except Exception as e:
            logger.error(f"Error getting key {key_id}: {e}")
            return None
    
    @staticmethod
    def get_key_by_value(key: str) -> Optional[WBApiKey]:
        """Получение ключа по значению с обработкой ошибок БД"""
        try:
            def query_key():
                return WBApiKey.query.filter_by(key=key).first()
            
            return KeyManager._execute_with_retry(query_key)
        except Exception as e:
            logger.error(f"Error getting key by value: {e}")
            return None
    
    @staticmethod
    def delete_key_permanently(key_id: int) -> Tuple[bool, str]:
        """Полное удаление ключа из базы данных"""
        try:
            def delete_key():
                key = WBApiKey.query.get(key_id)
                if not key:
                    return False, "Ключ не найден"
                
                # Удаляем отметки товаров для этого ключа
                SelectedProduct.query.filter_by(key_id=key_id).delete()
                
                # Удаляем товары, которые не используются другими ключами
                products = WBProduct.query.filter_by(key_id=key_id).all()
                for product in products:
                    other_selections = SelectedProduct.query.filter_by(product_id=product.id).filter(SelectedProduct.key_id != key_id).first()
                    if not other_selections:
                        db.session.delete(product)
                    else:
                        product.key_id = None
                
                # Удаляем логи для этого ключа
                WBApiLog.query.filter_by(key_id=key_id).delete()
                
                # Удаляем сам ключ
                db.session.delete(key)
                db.session.commit()
                return True, "Ключ успешно удалён из базы данных"
            
            success, message = KeyManager._execute_with_retry(delete_key)
            return success, message
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error deleting key {key_id}: {e}")
            return False, f"Ошибка при удалении ключа: {str(e)}"
    
    @staticmethod
    def restore_key(key_id: int) -> Tuple[bool, str]:
        """Восстановление удалённого (неактивного) ключа"""
        try:
            def restore():
                key = WBApiKey.query.get(key_id)
                if not key:
                    return False, "Ключ не найден"
                
                if key.is_active:
                    return False, "Ключ уже активен"
                
                key.is_active = True
                db.session.commit()
                return True, f"Ключ '{key.name}' успешно восстановлен"
            
            return KeyManager._execute_with_retry(restore)
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error restoring key {key_id}: {e}")
            return False, f"Ошибка при восстановлении ключа: {str(e)}"
    
    @staticmethod
    def deactivate_key(key_id: int) -> Tuple[bool, str]:
        """Мягкое удаление (деактивация) ключа"""
        try:
            def deactivate():
                key = WBApiKey.query.get(key_id)
                if not key:
                    return False, "Ключ не найден"
                
                if not key.is_active:
                    return False, "Ключ уже неактивен"
                
                key.is_active = False
                db.session.commit()
                return True, f"Ключ '{key.name}' деактивирован (скрыт из списка)"
            
            return KeyManager._execute_with_retry(deactivate)
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error deactivating key {key_id}: {e}")
            return False, f"Ошибка при деактивации ключа: {str(e)}"
    
    @staticmethod
    def check_key_connection(key_id: int) -> Tuple[bool, str, Optional[Dict]]:
        """Проверка подключения по ключу"""
        try:
            def get_key():
                return WBApiKey.query.get(key_id)
            
            key = KeyManager._execute_with_retry(get_key)
            if not key or not key.is_active:
                return False, "Ключ не найден или неактивен", None
            
            wb_api = WBApiService(key.key)
            
            # Проверяем подключение
            success, message, details = wb_api.check_connection()
            
            # Обновляем информацию о проверке
            def update_key():
                key.last_checked = datetime.utcnow()
                log = WBApiLog(
                    key_id=key_id,
                    endpoint='/ping',
                    method='GET',
                    status_code=details.get('status_code', 200 if success else 500),
                    response_time=details.get('response_time'),
                    error_message=message if not success else None
                )
                db.session.add(log)
                db.session.commit()
            
            KeyManager._execute_with_retry(update_key)
            
            return success, message, details
            
        except Exception as e:
            logger.error(f"Error checking connection for key {key_id}: {e}")
            return False, f"Ошибка проверки: {str(e)}", None
    
    @staticmethod
    def check_all_keys() -> Dict[int, Dict]:
        """Проверка всех активных ключей"""
        results = {}
        keys = KeyManager.get_all_keys(include_inactive=False)
        
        for key in keys:
            success, message, details = KeyManager.check_key_connection(key.id)
            results[key.id] = {
                'name': key.name,
                'success': success,
                'message': message,
                'details': details
            }
            # Небольшая задержка между проверками
            time.sleep(0.3)
        
        return results
    
    @staticmethod
    def get_key_full_info(key_id: int) -> Dict:
        """Получение полной информации о ключе"""
        try:
            def get_info():
                key = WBApiKey.query.get(key_id)
                if not key:
                    return {'error': 'Ключ не найден'}
                
                wb_api = WBApiService(key.key)
                
                # Получаем информацию о токене
                token_info = wb_api.get_token_info()
                
                # Проверяем все категории
                categories_status = wb_api.check_all_categories()
                
                # Получаем информацию о продавце
                seller_success, seller_info = wb_api.get_seller_info()
                
                # Получаем логи
                logs = WBApiLog.query.filter_by(key_id=key_id).order_by(WBApiLog.created_at.desc()).limit(20).all()
                
                return {
                    'key_info': key.to_dict(),
                    'token_info': token_info,
                    'categories_status': categories_status,
                    'seller_info': seller_info if seller_success else {'error': 'Не удалось получить информацию'},
                    'logs': [
                        {
                            'endpoint': log.endpoint,
                            'method': log.method,
                            'status_code': log.status_code,
                            'response_time': log.response_time,
                            'error': log.error_message,
                            'created_at': log.created_at.strftime('%Y-%m-%d %H:%M:%S')
                        }
                        for log in logs
                    ]
                }
            
            return KeyManager._execute_with_retry(get_info)
            
        except Exception as e:
            logger.error(f"Error getting full info for key {key_id}: {e}")
            return {'error': f'Ошибка получения информации: {str(e)}'}
    
    @staticmethod
    def get_keys_with_content_access() -> List[WBApiKey]:
        """Получение всех активных ключей с доступом к Контенту"""
        try:
            def query_keys():
                keys = WBApiKey.query.filter_by(is_active=True).all()
                return [key for key in keys if key.access_info.get('access_categories', {}).get('content', False)]
            
            return KeyManager._execute_with_retry(query_keys)
        except Exception as e:
            logger.error(f"Error getting keys with content access: {e}")
            return []
    
    @staticmethod
    def get_keys_count() -> Dict[str, int]:
        """Получение статистики по ключам"""
        try:
            def query_counts():
                total = WBApiKey.query.count()
                active = WBApiKey.query.filter_by(is_active=True).count()
                inactive = WBApiKey.query.filter_by(is_active=False).count()
                return {'total': total, 'active': active, 'inactive': inactive}
            
            return KeyManager._execute_with_retry(query_counts)
        except Exception as e:
            logger.error(f"Error getting keys count: {e}")
            return {'total': 0, 'active': 0, 'inactive': 0}