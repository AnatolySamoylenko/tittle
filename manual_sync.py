#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Скрипт для ручной синхронизации рекламных кампаний.
Можно запустить для тестирования.
"""

import os
import sys
from datetime import datetime
import pytz

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Импортируем функцию из scheduler_50
from scheduler_50 import run_sync
from app import app, db
from models import WBApiKey, SelectedProduct

if __name__ == "__main__":
    print("=" * 60)
    print(f"Ручная синхронизация рекламных кампаний")
    print(f"Время: {datetime.now(pytz.timezone('Europe/Moscow')).strftime('%Y-%m-%d %H:%M:%S MSK')}")
    print("=" * 60)
    
    with app.app_context():
        # Проверяем наличие ключей
        keys = WBApiKey.query.filter_by(is_active=True).all()
        print(f"Активных ключей: {len(keys)}")
        
        # Проверяем наличие отмеченных товаров
        selected = SelectedProduct.query.all()
        print(f"Отмеченных товаров: {len(selected)}")
        if selected:
            nm_ids = [s.nm_id for s in selected]
            nm_preview = ', '.join(str(n) for n in nm_ids[:10])
            if len(nm_ids) > 10:
                nm_preview += f"... и еще {len(nm_ids) - 10}"
            print(f"Артикулы: {nm_preview}")
    
    print("\nЗапуск синхронизации...\n")
    run_sync()
    print("\nСинхронизация завершена!")