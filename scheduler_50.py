#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Скрипт для синхронизации рекламных кампаний Wildberries.
Запускается по расписанию в 50 минут каждого часа.
"""

import os
import sys
import logging
import time
from datetime import datetime
import pytz

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Добавляем текущую директорию в path для импорта модулей
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Импортируем наши модули
from app import app, db
from models import WBApiKey, SelectedProduct, WBProduct
from services.advert_sync_service import AdvertSyncService


def run_sync():
    """Основная функция синхронизации"""
    task_name = os.environ.get('TASK_NAME', 'tittle_restart_50')
    moscow_tz = pytz.timezone('Europe/Moscow')
    current_time = datetime.now(moscow_tz)
    
    logger.info("=" * 60)
    logger.info(f"Запуск задачи: {task_name}")
    logger.info(f"Время запуска: {current_time.strftime('%Y-%m-%d %H:%M:%S MSK')}")
    logger.info(f"Часовой пояс: {current_time.tzinfo}")
    logger.info(f"Скрипт: scheduler_50.py")
    logger.info("=" * 60)

    with app.app_context():
        try:
            # Получаем все активные ключи с доступом к рекламе
            keys = WBApiKey.query.filter_by(is_active=True).all()
            
            # Фильтруем ключи с доступом к рекламе
            advert_keys = []
            for key in keys:
                access = key.access_info.get('access_categories', {})
                if access.get('advert', False):
                    advert_keys.append(key)
            
            logger.info(f"Найдено активных ключей: {len(keys)}")
            logger.info(f"Ключей с доступом к рекламе: {len(advert_keys)}")
            
            if not advert_keys:
                logger.warning("Нет ключей с доступом к рекламе. Завершение.")
                return
            
            # Для каждого ключа получаем отмеченные товары и синхронизируем кампании
            total_campaigns = 0
            total_updated = 0
            total_errors = 0
            
            for key in advert_keys:
                logger.info(f"\n--- Обработка ключа: {key.name} (ID: {key.id}) ---")
                logger.info(f"Тип токена: {key.token_type}")
                
                # Получаем отмеченные товары для этого ключа
                selected_products = SelectedProduct.query.filter_by(key_id=key.id).all()
                if not selected_products:
                    logger.info(f"Нет отмеченных товаров для ключа {key.name}")
                    continue
                
                nm_ids = [sp.nm_id for sp in selected_products]
                logger.info(f"Отмеченных товаров: {len(nm_ids)}")
                
                # Логируем первые 10 артикулов для контроля
                nm_preview = ', '.join(str(n) for n in nm_ids[:10])
                if len(nm_ids) > 10:
                    nm_preview += f"... и еще {len(nm_ids) - 10}"
                logger.info(f"Артикулы: {nm_preview}")
                
                # Создаем сервис для работы с рекламой
                advert_service = AdvertSyncService(key.key)
                
                # Получаем все кампании продавца
                campaigns = advert_service.get_all_campaigns()
                if campaigns is None:
                    logger.error(f"Не удалось получить кампании для ключа {key.name}")
                    total_errors += 1
                    continue
                
                logger.info(f"Найдено всего кампаний: {len(campaigns)}")
                
                # Фильтруем кампании, связанные с отмеченными товарами
                related_campaigns = []
                nm_ids_set = set(nm_ids)
                
                for campaign in campaigns:
                    campaign_id = campaign.get('id')
                    if not campaign_id:
                        continue
                    
                    # Получаем артикулы товаров в кампании
                    campaign_nm_ids = advert_service.get_campaign_nm_ids(campaign_id)
                    if campaign_nm_ids:
                        # Проверяем пересечение с отмеченными товарами
                        intersection = set(campaign_nm_ids) & nm_ids_set
                        if intersection:
                            campaign['related_nm_ids'] = list(intersection)
                            campaign['all_nm_ids'] = campaign_nm_ids
                            related_campaigns.append(campaign)
                            logger.info(f"  - Кампания {campaign_id} содержит {len(intersection)} отмеченных товаров из {len(campaign_nm_ids)}")
                
                logger.info(f"Кампаний, связанных с отмеченными товарами: {len(related_campaigns)}")
                
                if related_campaigns:
                    # Сохраняем информацию о кампаниях в базу данных
                    for campaign in related_campaigns:
                        success = advert_service.save_campaign_to_db(campaign, key.id)
                        if success:
                            total_updated += 1
                        total_campaigns += 1
                        
                        # Небольшая задержка между запросами к API
                        time.sleep(0.3)
                    
                    logger.info(f"Сохранено/обновлено кампаний для ключа {key.name}: {len(related_campaigns)}")
                else:
                    logger.info(f"Нет кампаний, связанных с отмеченными товарами для ключа {key.name}")
            
            # Итоговый отчет
            logger.info("\n" + "=" * 60)
            logger.info("СИНХРОНИЗАЦИЯ ЗАВЕРШЕНА")
            logger.info(f"Всего обработано кампаний: {total_campaigns}")
            logger.info(f"Обновлено в БД: {total_updated}")
            logger.info(f"Ошибок: {total_errors}")
            logger.info(f"Время завершения: {datetime.now(moscow_tz).strftime('%Y-%m-%d %H:%M:%S MSK')}")
            logger.info("=" * 60)
            
        except Exception as e:
            logger.error(f"Критическая ошибка в синхронизации: {e}")
            import traceback
            traceback.print_exc()


def main():
    """Точка входа"""
    # Проверяем, что мы в cron-режиме
    if os.environ.get('CRONJOB_MODE') != 'true':
        logger.warning("Запуск в режиме разработки...")
        # Для тестирования можно принудительно включить режим
        # os.environ['CRONJOB_MODE'] = 'true'
    
    run_sync()


if __name__ == "__main__":
    main()