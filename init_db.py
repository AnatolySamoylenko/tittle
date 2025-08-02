cd ~/mysite
echo 'from flask_app import app, db
with app.app_context():
    db.create_all()
    print("Таблицы созданы успешно!")' > init_db.py