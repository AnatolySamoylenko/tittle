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
    advert_campaigns = db.relationship('AdvertCampaign', backref='key', lazy='dynamic')
    advert_campaign_nms = db.relationship('AdvertCampaignNM', backref='key', lazy='dynamic')
    
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
    nm_id = db.Column(db.BigInteger, nullable=False)
    key_id = db.Column(db.Integer, db.ForeignKey('wb_api_keys.id'), nullable=False)
    selected_at = db.Column(db.DateTime, default=datetime.utcnow)
    
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


class AdvertCampaign(db.Model):
    """Модель для хранения рекламных кампаний Wildberries"""
    __tablename__ = 'advert_campaigns'
    
    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.BigInteger, nullable=False)
    key_id = db.Column(db.Integer, db.ForeignKey('wb_api_keys.id'), nullable=False)
    
    name = db.Column(db.String(500))
    status = db.Column(db.Integer)
    status_name = db.Column(db.String(50))
    advert_type = db.Column(db.String(50))
    payment_type = db.Column(db.String(20))
    
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_synced = db.Column(db.DateTime, default=datetime.utcnow)
    
    raw_data = db.Column(db.JSON, default=dict)
    
    # Исправленная связь с AdvertCampaignNM
    # Используем primaryjoin для явного указания условия соединения
    nms = db.relationship(
        'AdvertCampaignNM',
        primaryjoin='AdvertCampaign.campaign_id == AdvertCampaignNM.campaign_id',
        foreign_keys='AdvertCampaignNM.campaign_id',
        backref='campaign_ref',
        lazy='dynamic',
        viewonly=False,
        overlaps='campaign_ref'
    )
    
    __table_args__ = (
        db.UniqueConstraint('campaign_id', 'key_id', name='unique_campaign_key'),
    )
    
    def __repr__(self):
        return f'<AdvertCampaign {self.campaign_id} - {self.name or "Без названия"}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'campaign_id': self.campaign_id,
            'key_id': self.key_id,
            'name': self.name,
            'status': self.status,
            'status_name': self.status_name,
            'advert_type': self.advert_type,
            'payment_type': self.payment_type,
            'is_active': self.is_active,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M') if self.created_at else None,
            'updated_at': self.updated_at.strftime('%Y-%m-%d %H:%M') if self.updated_at else None,
            'last_synced': self.last_synced.strftime('%Y-%m-%d %H:%M') if self.last_synced else None,
            'nms_count': self.nms.count()
        }
    
    def get_status_display(self):
        status_names = {
            -1: 'удалена',
            4: 'готова к запуску',
            7: 'завершена',
            8: 'отменена',
            9: 'активна',
            11: 'на паузе'
        }
        return status_names.get(self.status, 'неизвестно')


class AdvertCampaignNM(db.Model):
    """Модель для хранения артикулов товаров в рекламных кампаниях"""
    __tablename__ = 'advert_campaign_nms'
    
    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.BigInteger, nullable=False)
    nm_id = db.Column(db.BigInteger, nullable=False)
    key_id = db.Column(db.Integer, db.ForeignKey('wb_api_keys.id'), nullable=False)
    
    bids = db.Column(db.JSON, default=dict)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Связь с продуктом (опционально)
    product = db.relationship(
        'WBProduct',
        foreign_keys=[nm_id],
        primaryjoin='WBProduct.nm_id == AdvertCampaignNM.nm_id',
        viewonly=True,
        uselist=False
    )
    
    __table_args__ = (
        db.UniqueConstraint('campaign_id', 'nm_id', 'key_id', name='unique_campaign_nm_key'),
    )
    
    def __repr__(self):
        return f'<AdvertCampaignNM campaign={self.campaign_id} nm={self.nm_id}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'campaign_id': self.campaign_id,
            'nm_id': self.nm_id,
            'key_id': self.key_id,
            'bids': self.bids,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M') if self.created_at else None,
            'updated_at': self.updated_at.strftime('%Y-%m-%d %H:%M') if self.updated_at else None
        }
    
    def get_bid_for_placement(self, placement: str) -> int:
        if not self.bids:
            return 0
        return self.bids.get(placement, 0)