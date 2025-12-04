from flask_login import UserMixin
from sqlalchemy import Integer, String, ForeignKey, func, UniqueConstraint, text, Column, Computed
from sqlalchemy import Time as SQLTime, DateTime as SQLDateTime
from sqlalchemy.orm import relationship
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from .extensions import db

# Userクラスはここに置いてOK
class User(UserMixin):
    def __init__(self, id, username, password):
        self.id = id
        self.username = username
        self.password = password
        
    def get_id(self):
        return f"admin-{self.id}"

# ★修正: admin_user_db の定義は __init__.py にあるので、ここでは削除しました

class 教室(db.Model):
    __tablename__ = '教室'
    教室ID = db.Column(db.Integer, primary_key=True)
    教室名 = db.Column(db.String, nullable=False)
    授業s = db.relationship('授業', back_populates='教室')

class LineUser(db.Model):
    __tablename__ = 'line_user'
    line_user_id = db.Column(db.String(50), primary_key=True) 
    student_id = db.Column(db.Integer, db.ForeignKey('学生.学生ID'), unique=True, nullable=False)
    student = relationship("学生", back_populates="line_user")
    
class FaceData(db.Model):
    __tablename__ = 'face_data'
    student_id = db.Column(db.Integer, db.ForeignKey('学生.学生ID', ondelete='CASCADE'), primary_key=True)
    face_encoding = db.Column(db.Text, nullable=False) 
    student = relationship("学生", back_populates="face_data")    

class 学生(UserMixin, db.Model):
    __tablename__ = '学生'
    学生ID = db.Column(db.Integer, primary_key=True)
    学生名 = db.Column(db.String, nullable=False, unique=True)
    
    password_hash = db.Column(db.String(256), nullable=True) 
    parent_email = db.Column(db.String(120), nullable=True)

    出席記録s = db.relationship('出席記録', back_populates='学生', cascade="all, delete-orphan")
    在室履歴s = db.relationship('在室履歴', back_populates='学生', cascade="all, delete-orphan")
    line_user = relationship("LineUser", back_populates="student", uselist=False, cascade="all, delete-orphan")
    face_data = relationship("FaceData", back_populates="student", uselist=False, cascade="all, delete-orphan")

    def get_id(self):
        return f"student-{self.学生ID}"

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        if self.password_hash is None:
            return False
        return check_password_hash(self.password_hash, password)

class 授業(db.Model):
    __tablename__ = '授業'
    授業ID = db.Column(db.Integer, primary_key=True)
    授業科目名 = db.Column(db.String, nullable=False)
    担当教員 = db.Column(db.String)
    教室ID = db.Column(db.Integer, db.ForeignKey('教室.教室ID'))
    教室 = db.relationship('教室', back_populates='授業s')
    時間割s = db.relationship('時間割', back_populates='授業', cascade="all, delete-orphan")
    出席記録s = db.relationship('出席記録', back_populates='授業', cascade="all, delete-orphan")

class TimeTable(db.Model):
    __tablename__ = 'TimeTable'
    時限 = db.Column(db.Integer, primary_key=True)
    開始時刻 = db.Column(SQLTime, nullable=False)
    終了時刻 = db.Column(SQLTime, nullable=False)
    備考 = db.Column(db.String)

class 授業計画(db.Model):
    __tablename__ = '授業計画'
    日付 = db.Column(db.String, primary_key=True)
    期 = db.Column(db.Integer)
    授業曜日 = db.Column(db.Integer)
    備考 = db.Column(db.String)

class 時間割(db.Model):
    __tablename__ = '時間割'
    時間割ID = db.Column(db.Integer, primary_key=True, autoincrement=True)
    学期 = db.Column(db.String) 
    曜日 = db.Column(db.String) 
    時限 = db.Column(db.Integer)
    授業ID = db.Column(db.Integer, db.ForeignKey('授業.授業ID'))
    備考 = db.Column(db.String)
    授業 = db.relationship('授業', back_populates='時間割s')
    __table_args__ = (UniqueConstraint('学期', '曜日', '時限', name='_gaku_yobi_jigen_uc'),)

class 出席記録(db.Model):
    __tablename__ = '出席記録'
    ID = db.Column(db.Integer, primary_key=True, autoincrement=True)
    学生ID = db.Column(db.Integer, db.ForeignKey('学生.学生ID', ondelete='CASCADE'), nullable=False)
    授業ID = db.Column(db.Integer, db.ForeignKey('授業.授業ID', ondelete='CASCADE'), nullable=False)
    出席時刻 = db.Column(SQLDateTime, nullable=False, default=datetime.now)
    状態 = db.Column(db.String, nullable=False) 
    時限 = db.Column(db.Integer, nullable=False)
    出席日付 = Column(SQLDateTime, Computed(func.date(出席時刻)))
    学生 = db.relationship('学生', back_populates='出席記録s')
    授業 = db.relationship('授業', back_populates='出席記録s')
    __table_args__ = (
        UniqueConstraint('学生ID', '授業ID', '時限', '出席日付', name='_student_class_period_date_uc'),
    )

class 在室履歴(db.Model):
    __tablename__ = '在室履歴'
    履歴ID = db.Column(db.Integer, primary_key=True, autoincrement=True)
    学生ID = db.Column(db.Integer, db.ForeignKey('学生.学生ID', ondelete='CASCADE'), nullable=False)
    教室ID = db.Column(db.Integer, db.ForeignKey('教室.教室ID'))
    入室時刻 = db.Column(SQLDateTime, nullable=False, default=datetime.now)
    退室時刻 = db.Column(SQLDateTime, nullable=True)
    備考 = db.Column(db.String(50), nullable=True)
    学生 = db.relationship('学生', back_populates='在室履歴s')
    教室 = db.relationship('教室', foreign_keys=[教室ID])

class 時間割_デフォルト(db.Model):
    __tablename__ = '時間割_デフォルト'
    時間割ID = db.Column(db.Integer, primary_key=True, autoincrement=True)
    学期 = db.Column(db.String)
    曜日 = db.Column(db.String)
    時限 = db.Column(db.Integer)
    授業ID = db.Column(db.Integer)
    備考 = db.Column(db.String)
    __table_args__ = (UniqueConstraint('学期', '曜日', '時限', name='_default_gaku_yobi_jigen_uc'),)

class ReportRecord(db.Model):
    __tablename__ = 'report_record'
    record_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    student_id = db.Column(db.Integer, db.ForeignKey('学生.学生ID'), nullable=False)
    report_type = db.Column(db.String(10), nullable=False)
    reason = db.Column(db.String(500), nullable=True)
    report_date = db.Column(SQLDateTime, nullable=False, default=datetime.now)
    is_resolved = db.Column(db.Boolean, default=False)
    student = relationship("学生")

class 日別時間割(db.Model):
    __tablename__ = '日別時間割'
    ID = db.Column(db.Integer, primary_key=True, autoincrement=True)
    日付 = db.Column(db.String, nullable=False)
    時限 = db.Column(db.Integer, nullable=False)
    授業ID = db.Column(db.Integer, db.ForeignKey('授業.授業ID'))
    教室ID = db.Column(db.Integer, db.ForeignKey('教室.教室ID')) 
    備考 = db.Column(db.String)
    授業 = db.relationship('授業', foreign_keys=[授業ID])
    教室 = db.relationship('教室', foreign_keys=[教室ID])
    __table_args__ = (UniqueConstraint('日付', '時限', name='_daily_jigen_uc'),)