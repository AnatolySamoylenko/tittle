import requests
import logging
import time
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from models import db, AdvertCampaign, AdvertCampaignNM, WBApiKey
from sqlalchemy.exc import OperationalError, DisconnectionError
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class AdvertSyncService:
    """Сервис для синхронизации рекламных кампаний Wildberries"""
    
    BASE_URL = 'https://advert-api.wildberries.ru'
    
    # Маппинг статусов кампаний
    STATUS_MAP = {
        -1: 'deleted',
        4: 'ready',
        7: 'completed',
        8: 'canceled',
        9: 'active',
        11: 'paused'
    }
    
    STATUS_NAMES = {
        -1: 'удалена',
        4: 'готова к запуску',
        7: 'завершена',
        8: 'отменена',
        9: 'активна',
        11: 'на паузе'
    }
    
    # Типы кампаний
    ADVERT_TYPES = {
        8: 'unified',
        9: 'manual'
    }
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {
            'Authorization': api_key,
            'Content-Type': 'application/json'
        }
        
        # Настраиваем сессию с повторными попытками
        self.session = requests.Session()
        retries = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[408, 429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
            raise_on_status=False
        )
        adapter = HTTPAdapter(
            max_retries=retries,
            pool_connections=10,
            pool_maxsize=10
        )
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)
        
        self.timeout = (10, 30)
    
    def _execute_with_retry(self, func, retries=5, delay=1):
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
    
    def get_all_campaigns(self) -> Optional[List[Dict]]:
        """
        Получение всех рекламных кампаний продавца.
        Использует /adv/v1/promotion/count и /api/advert/v2/adverts
        """
        try:
            # Сначала получаем список ID кампаний
            count_url = f"{self.BASE_URL}/adv/v1/promotion/count"
            count_response = self.session.get(count_url, headers=self.headers, timeout=self.timeout)
            
            if count_response.status_code != 200:
                logger.error(f"Error getting campaign count: {count_response.status_code}")
                if count_response.text:
                    logger.error(f"Response: {count_response.text[:500]}")
                return None
            
            count_data = count_response.json()
            
            # Собираем все ID кампаний из всех групп
            all_advert_ids = []
            for group in count_data.get('adverts', []):
                for advert in group.get('advert_list', []):
                    all_advert_ids.append(advert['advertId'])
            
            if not all_advert_ids:
                logger.info("Нет рекламных кампаний")
                return []
            
            logger.info(f"Найдено кампаний: {len(all_advert_ids)}")
            
            # Получаем детальную информацию о кампаниях
            # Разбиваем ID на группы по 50 (ограничение API)
            all_campaigns = []
            for i in range(0, len(all_advert_ids), 50):
                batch_ids = all_advert_ids[i:i+50]
                ids_str = ','.join(str(id) for id in batch_ids)
                
                details_url = f"{self.BASE_URL}/api/advert/v2/adverts?ids={ids_str}"
                details_response = self.session.get(details_url, headers=self.headers, timeout=self.timeout)
                
                if details_response.status_code == 200:
                    details_data = details_response.json()
                    campaigns = details_data.get('adverts', [])
                    all_campaigns.extend(campaigns)
                    logger.info(f"Загружено деталей для {len(campaigns)} кампаний (batch {i//50 + 1})")
                else:
                    logger.error(f"Error getting campaign details: {details_response.status_code}")
                    if details_response.text:
                        logger.error(f"Response: {details_response.text[:500]}")
                
                # Задержка между запросами
                time.sleep(0.3)
            
            logger.info(f"Всего получено деталей кампаний: {len(all_campaigns)}")
            return all_campaigns
            
        except requests.exceptions.Timeout:
            logger.error("Timeout getting campaigns")
            return None
        except requests.exceptions.ConnectionError:
            logger.error("Connection error getting campaigns")
            return None
        except Exception as e:
            logger.error(f"Error getting campaigns: {e}")
            return None
    
    def get_campaign_nm_ids(self, campaign_id: int) -> List[int]:
        """
        Получение списка артикулов товаров в кампании
        """
        try:
            # Используем детальную информацию о кампании
            url = f"{self.BASE_URL}/api/advert/v2/adverts?ids={campaign_id}"
            response = self.session.get(url, headers=self.headers, timeout=(5, 15))
            
            if response.status_code != 200:
                return []
            
            data = response.json()
            campaigns = data.get('adverts', [])
            
            nm_ids = []
            for campaign in campaigns:
                nm_settings = campaign.get('nm_settings', [])
                for setting in nm_settings:
                    nm_id = setting.get('nm_id')
                    if nm_id:
                        nm_ids.append(nm_id)
            
            return nm_ids
            
        except Exception as e:
            logger.error(f"Error getting campaign NM IDs for {campaign_id}: {e}")
            return []
    
    def save_campaign_to_db(self, campaign_data: Dict, key_id: int) -> bool:
        """
        Сохранение информации о кампании в базу данных
        """
        try:
            campaign_id = campaign_data.get('id')
            if not campaign_id:
                logger.error("Campaign data missing 'id'")
                return False
            
            def save():
                # Проверяем существование ключа
                key = WBApiKey.query.get(key_id)
                if not key:
                    logger.error(f"Key {key_id} not found")
                    return False
                
                # Проверяем, существует ли уже кампания
                existing = AdvertCampaign.query.filter_by(
                    campaign_id=campaign_id,
                    key_id=key_id
                ).first()
                
                status = campaign_data.get('status', 0)
                settings = campaign_data.get('settings', {})
                nm_settings = campaign_data.get('nm_settings', [])
                
                if existing:
                    # Обновляем существующую
                    existing.name = settings.get('name', '')
                    existing.status = status
                    existing.status_name = self.STATUS_NAMES.get(status, 'неизвестно')
                    existing.advert_type = campaign_data.get('bid_type', '')
                    existing.payment_type = settings.get('payment_type', '')
                    existing.updated_at = datetime.utcnow()
                    existing.last_synced = datetime.utcnow()
                    existing.is_active = status in [4, 9, 11]
                    existing.raw_data = campaign_data
                    
                    # Обновляем связанные NM
                    AdvertCampaignNM.query.filter_by(
                        campaign_id=campaign_id,
                        key_id=key_id
                    ).delete()
                    
                    # Добавляем новые NM
                    for setting in nm_settings:
                        nm_id = setting.get('nm_id')
                        if nm_id:
                            nm = AdvertCampaignNM(
                                campaign_id=campaign_id,
                                nm_id=nm_id,
                                key_id=key_id,
                                bids=setting.get('bids_kopecks', {})
                            )
                            db.session.add(nm)
                    
                    logger.info(f"Обновлена кампания {campaign_id} ({existing.name})")
                else:
                    # Создаем новую кампанию
                    campaign = AdvertCampaign(
                        campaign_id=campaign_id,
                        key_id=key_id,
                        name=settings.get('name', ''),
                        status=status,
                        status_name=self.STATUS_NAMES.get(status, 'неизвестно'),
                        advert_type=campaign_data.get('bid_type', ''),
                        payment_type=settings.get('payment_type', ''),
                        is_active=status in [4, 9, 11],
                        raw_data=campaign_data
                    )
                    db.session.add(campaign)
                    db.session.flush()  # Получаем ID
                    
                    # Добавляем NM
                    for setting in nm_settings:
                        nm_id = setting.get('nm_id')
                        if nm_id:
                            nm = AdvertCampaignNM(
                                campaign_id=campaign_id,
                                nm_id=nm_id,
                                key_id=key_id,
                                bids=setting.get('bids_kopecks', {})
                            )
                            db.session.add(nm)
                    
                    logger.info(f"Создана новая кампания {campaign_id} ({campaign.name})")
                
                db.session.commit()
                return True
            
            return self._execute_with_retry(save)
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error saving campaign {campaign_id}: {e}")
            return False


class CampaignStatus:
    """Константы статусов кампаний"""
    DELETED = -1
    READY = 4
    COMPLETED = 7
    CANCELED = 8
    ACTIVE = 9
    PAUSED = 11
    
    @classmethod
    def get_name(cls, status: int) -> str:
        return {
            cls.DELETED: 'удалена',
            cls.READY: 'готова к запуску',
            cls.COMPLETED: 'завершена',
            cls.CANCELED: 'отменена',
            cls.ACTIVE: 'активна',
            cls.PAUSED: 'на паузе'
        }.get(status, 'неизвестно')
    
    @classmethod
    def is_active_status(cls, status: int) -> bool:
        return status in [cls.READY, cls.ACTIVE, cls.PAUSED]