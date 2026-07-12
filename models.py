from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import json

db = SQLAlchemy()

class WBApiKey(db.Model):
    """Модель для хранения API ключей Wildberries"""
    __tablename__ = 'wb_api_keys'
    
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(500), nullable=False, unique=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.String(500))
    token_type = db.Column(db.String(50))  # personal, service, base, test
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_checked = db.Column(db.DateTime)
    is_active = db.Column(db.Boolean, default=True)
    
    # Храним информацию о доступах в JSON
    access_info = db.Column(db.JSON, default=dict)
    
    def __repr__(self):
        return f'<WBApiKey {self.name}>'
    
    def to_dict(self):
        """Преобразование в словарь для отображения"""
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'token_type': self.token_type,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M') if self.created_at else None,
            'last_checked': self.last_checked.strftime('%Y-%m-%d %H:%M') if self.last_checked else None,
            'is_active': self.is_active,
            'access_info': self.access_info or {}
        }

class WBApiLog(db.Model):
    """Логи запросов к API"""
    __tablename__ = 'wb_api_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    key_id = db.Column(db.Integer, db.ForeignKey('wb_api_keys.id'))
    endpoint = db.Column(db.String(200))
    method = db.Column(db.String(10))
    status_code = db.Column(db.Integer)
    response_time = db.Column(db.Float)  # в секундах
    error_message = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    key = db.relationship('WBApiKey', backref='logs')