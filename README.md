# Управление Wildberries API

Веб-приложение для управления API ключами Wildberries с тремя разделами меню.

## Возможности

### 🔑 Настройки (Управление ключами)
- Добавление API ключей с указанием названия и описания
- Автоматическое определение типа токена (personal, service, base, test)
- Проверка доступов к разделам кабинета
- Просмотр полной информации по ключу
- Удаление ключей из базы данных
- Хранение в PostgreSQL

### 📊 Управление рекламой (в разработке)
### 📦 Управление товарами (в разработке)

## Технологии
- Flask 2.3.3
- SQLAlchemy (ORM)
- PostgreSQL
- Bootstrap 5
- JWT декодирование

## Локальный запуск

```bash
# Клонирование репозитория
git clone https://github.com/AnatolySamoylenko/wbapps.git
cd wbapps/hello-anatolyt

# Установка зависимостей
pip install -r requirements.txt

# Запуск
python app.py