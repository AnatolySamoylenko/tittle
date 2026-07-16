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
    token_type = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_checked = db.Column(db.DateTime)
    is_active = db.Column(db.Boolean, default=True)
    access_info = db.Column(db.JSON, default=dict)
    
    products = db.relationship('WBProduct', backref='key', lazy='dynamic')
    selected_products = db.relationship('SelectedProduct', backref='key', lazy='dynamic')
    logs = db.relationship('WBApiLog', backref='key', lazy='dynamic')
    
    def __repr__(self):
        return f'<WBApiKey {self.name}>'
    
    def to_dict(self):
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
    response_time = db.Column(db.Float)
    error_message = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<WBApiLog {self.endpoint} - {self.status_code}>'


class WBProduct(db.Model):
    """Модель для хранения товаров из Wildberries (основная информация)"""
    __tablename__ = 'wb_products'
    
    id = db.Column(db.Integer, primary_key=True)
    nm_id = db.Column(db.BigInteger, unique=True, nullable=False)
    vendor_code = db.Column(db.String(100))
    title = db.Column(db.String(500))
    brand = db.Column(db.String(200))
    subject_name = db.Column(db.String(200))
    subject_id = db.Column(db.Integer)
    imt_id = db.Column(db.BigInteger)
    updated_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    key_id = db.Column(db.Integer, db.ForeignKey('wb_api_keys.id'))
    
    # УБИРАЕМ прямую связь с SelectedProduct через product_id
    # Вместо этого будем использовать запросы к SelectedProduct по nm_id
    
    def __repr__(self):
        return f'<WBProduct {self.nm_id} - {self.title[:30] if self.title else "No title"}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'nm_id': self.nm_id,
            'vendor_code': self.vendor_code,
            'title': self.title,
            'brand': self.brand,
            'subject_name': self.subject_name,
            'subject_id': self.subject_id,
            'imt_id': self.imt_id,
            'updated_at': self.updated_at.strftime('%Y-%m-%d %H:%M') if self.updated_at else None,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M') if self.created_at else None
        }


class SelectedProduct(db.Model):
    """Модель для хранения ОТМЕЧЕННЫХ товаров (только nm_id и key_id)"""
    __tablename__ = 'selected_products'
    
    id = db.Column(db.Integer, primary_key=True)
    nm_id = db.Column(db.BigInteger, nullable=False)  # Артикул WB
    key_id = db.Column(db.Integer, db.ForeignKey('wb_api_keys.id'), nullable=False)
    selected_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Уникальность: один товар может быть отмечен только один раз для одного ключа
    __table_args__ = (
        db.UniqueConstraint('nm_id', 'key_id', name='unique_selected_product'),
    )
    
    def __repr__(self):
        return f'<SelectedProduct nm_id={self.nm_id} key={self.key_id}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'nm_id': self.nm_id,
            'key_id': self.key_id,
            'selected_at': self.selected_at.strftime('%Y-%m-%d %H:%M') if self.selected_at else None
        }