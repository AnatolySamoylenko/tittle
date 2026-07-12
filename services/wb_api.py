import requests
import time
import jwt
import json
from datetime import datetime
from typing import Dict, Any, Optional, Tuple
import logging

logger = logging.getLogger(__name__)

class WBApiService:
    """Сервис для работы с API Wildberries"""
    
    # Базовые URL для разных категорий
    BASE_URLS = {
        'common': 'https://common-api.wildberries.ru',
        'content': 'https://content-api.wildberries.ru',
        'analytics': 'https://seller-analytics-api.wildberries.ru',
        'prices': 'https://discounts-prices-api.wildberries.ru',
        'marketplace': 'https://marketplace-api.wildberries.ru',
        'statistics': 'https://statistics-api.wildberries.ru',
        'advert': 'https://advert-api.wildberries.ru',
        'feedbacks': 'https://feedbacks-api.wildberries.ru',
        'chat': 'https://buyer-chat-api.wildberries.ru',
        'supplies': 'https://supplies-api.wildberries.ru',
        'returns': 'https://returns-api.wildberries.ru',
        'documents': 'https://documents-api.wildberries.ru',
        'finance': 'https://finance-api.wildberries.ru',
        'users': 'https://user-management-api.wildberries.ru'
    }
    
    # Категории доступа
    ACCESS_CATEGORIES = {
        'content': 'Контент',
        'analytics': 'Аналитика',
        'prices': 'Цены и скидки',
        'marketplace': 'Маркетплейс',
        'statistics': 'Статистика',
        'advert': 'Продвижение',
        'feedbacks': 'Вопросы и отзывы',
        'chat': 'Чат с покупателями',
        'supplies': 'Поставки',
        'returns': 'Возвраты',
        'documents': 'Документы',
        'finance': 'Финансы',
        'users': 'Пользователи',
        'common': 'Общее'
    }
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {
            'Authorization': api_key,
            'Content-Type': 'application/json'
        }
        
    def decode_token(self) -> Dict[str, Any]:
        """Декодирование JWT токена для получения информации"""
        try:
            # JWT состоит из трех частей: header.payload.signature
            parts = self.api_key.split('.')
            if len(parts) != 3:
                return {'error': 'Invalid JWT format'}
            
            # Декодируем payload (вторую часть)
            payload = parts[1]
            # Добавляем padding если нужно
            payload += '=' * (4 - len(payload) % 4)
            decoded = jwt.decode(self.api_key, options={'verify_signature': False})
            return decoded
        except Exception as e:
            logger.error(f"Error decoding token: {e}")
            return {'error': str(e)}
    
    def get_token_info(self) -> Dict[str, Any]:
        """Получение информации о токене"""
        decoded = self.decode_token()
        if 'error' in decoded:
            return decoded
            
        info = {
            'token_type': self._get_token_type(decoded),
            'seller_id': decoded.get('sid'),
            'expires_at': datetime.fromtimestamp(decoded.get('exp', 0)).isoformat() if decoded.get('exp') else None,
            'access_categories': self._get_access_categories(decoded)
        }
        return info
    
    def _get_token_type(self, decoded: Dict) -> str:
        """Определение типа токена"""
        acc = decoded.get('acc')
        token_for = decoded.get('for')
        is_test = decoded.get('t', False)
        
        if acc == 1 and not is_test:
            return 'base'
        elif acc == 2 and is_test:
            return 'test'
        elif acc == 3 and token_for == 'self':
            return 'personal'
        elif acc == 4 and token_for and token_for.startswith('asid:'):
            return 'service'
        else:
            return 'unknown'
    
    def _get_access_categories(self, decoded: Dict) -> Dict[str, bool]:
        """Получение категорий доступа из битовой маски"""
        s = decoded.get('s', 0)
        categories = {}
        
        # Битовая маска доступа
        bit_map = {
            1: 'content',      # Контент
            2: 'analytics',    # Аналитика
            3: 'prices',       # Цены и скидки
            4: 'marketplace',  # Маркетплейс
            5: 'statistics',   # Статистика
            6: 'advert',       # Продвижение
            7: 'feedbacks',    # Вопросы и отзывы
            9: 'chat',         # Чат с покупателями
            10: 'supplies',    # Поставки
            11: 'returns',     # Возвраты покупателями
            12: 'documents',   # Документы
            13: 'finance',     # Финансы
            16: 'users'        # Пользователи
        }
        
        for bit, category in bit_map.items():
            categories[category] = bool(s & (1 << bit))
        
        # Проверяем доступ только на чтение
        is_readonly = bool(s & (1 << 30))
        categories['readonly'] = is_readonly
        
        return categories
    
    def check_connection(self, category: str = 'common') -> Tuple[bool, str, Optional[Dict]]:
        """Проверка подключения к API"""
        base_url = self.BASE_URLS.get(category, self.BASE_URLS['common'])
        url = f"{base_url}/ping"
        
        try:
            start_time = time.time()
            response = requests.get(url, headers=self.headers, timeout=10)
            response_time = time.time() - start_time
            
            if response.status_code == 200:
                data = response.json()
                return True, "Подключение успешно", {
                    'status': data.get('Status'),
                    'timestamp': data.get('TS'),
                    'response_time': round(response_time, 3)
                }
            else:
                error_data = response.json() if response.text else {}
                return False, f"Ошибка {response.status_code}", {
                    'status_code': response.status_code,
                    'detail': error_data.get('detail', response.text),
                    'response_time': round(response_time, 3)
                }
        except requests.exceptions.Timeout:
            return False, "Превышено время ожидания", {'error': 'timeout'}
        except requests.exceptions.ConnectionError:
            return False, "Ошибка подключения к серверу", {'error': 'connection_error'}
        except Exception as e:
            return False, f"Неизвестная ошибка: {str(e)}", {'error': str(e)}
    
    def check_all_categories(self) -> Dict[str, Dict]:
        """Проверка доступа ко всем категориям"""
        results = {}
        
        for category, url in self.BASE_URLS.items():
            ping_url = f"{url}/ping"
            try:
                start_time = time.time()
                response = requests.get(ping_url, headers=self.headers, timeout=5)
                response_time = time.time() - start_time
                
                results[category] = {
                    'status': 'success' if response.status_code == 200 else 'error',
                    'status_code': response.status_code,
                    'response_time': round(response_time, 3)
                }
                
                if response.status_code == 200:
                    data = response.json()
                    results[category]['detail'] = data.get('Status', 'OK')
                else:
                    error_data = response.json() if response.text else {}
                    results[category]['detail'] = error_data.get('detail', response.text)
                    
            except Exception as e:
                results[category] = {
                    'status': 'error',
                    'detail': str(e),
                    'response_time': None
                }
            
            # Небольшая задержка чтобы не превысить лимиты
            time.sleep(0.2)
        
        return results
    
    def get_seller_info(self) -> Tuple[bool, Dict]:
        """Получение информации о продавце"""
        url = f"{self.BASE_URLS['common']}/api/v1/seller-info"
        
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                return True, data
            else:
                error_data = response.json() if response.text else {}
                return False, {'error': error_data.get('detail', response.text)}
        except Exception as e:
            return False, {'error': str(e)}