#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Точка входа для WSGI-сервера.
Запускает синхронизацию рекламных кампаний.
"""

from scheduler_50 import run_sync

if __name__ == "__main__":
    run_sync()