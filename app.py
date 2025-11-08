import os
from dotenv import load_dotenv # ⬅️ これを追加
from flask import Flask, render_template, request, jsonify, redirect, url_for, make_response, flash, get_flashed_messages, abort
from datetime import datetime, timedelta
from collections import OrderedDict
from urllib.parse import quote
# ... (Flask, datetime, csv などのimport) ...
from functools import wraps

# Flask-Mail
from flask_mail import Mail, Message

# Flask-Login
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user

from linebot import (
    LineBotApi, WebhookHandler
)
from linebot.exceptions import (
    InvalidSignatureError
)
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,
)
from linebot.models import QuickReply, QuickReplyButton, MessageAction

# --- ▼ SQLAlchemy (B案) に変更 ▼ ---
from flask_sqlalchemy import SQLAlchemy
# 以下の行が重要です。必要な型と関数だけをインポートします。
from sqlalchemy import Integer, String, ForeignKey, func, UniqueConstraint, text, Column, Computed 
from sqlalchemy import Time as SQLTime, DateTime as SQLDateTime
from sqlalchemy.orm import relationship
from sqlalchemy.exc import IntegrityError 
# --- ▲ SQLAlchemy に変更 ▲ ---

load_dotenv()
app = Flask(__name__)

# --- 1. データベース設定 (PostgreSQL/SQLite両対応) ---
DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///zaiseki.db')
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ECHO'] = False # Trueにすると実行SQLをコンソールに出力

db = SQLAlchemy(app)
# --- ▲ データベース設定ここまで ▲ ---

# --- ▼▼▼ ここにLINE Bot設定を追加 ▼▼▼ ---
# (LINE Developersコンソールから取得したキーを設定)
# ⚠️ 環境変数（os.environ.get）から読み込むことを強く推奨します
# --- ▼▼▼ LINE Bot設定 (修正) ▼▼▼ ---
YOUR_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')
YOUR_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET')

# (キーが存在する場合のみAPIを初期化する)
if YOUR_CHANNEL_ACCESS_TOKEN and YOUR_CHANNEL_SECRET:
    line_bot_api = LineBotApi(YOUR_CHANNEL_ACCESS_TOKEN)
    handler = WebhookHandler(YOUR_CHANNEL_SECRET)
else:
    # (init-db 実行時など、キーがない場合はダミーで初期化)
    line_bot_api = None 
    handler = None
    print("【WARNING】LINE Botのトークンが設定されていません。init-db を実行中...?")
# --- ▲▲▲ 修正ここまで ▲▲▲ ---

# --- 2. Flask-Login と Mail の設定 ---
app.secret_key = os.environ.get('SECRET_KEY', 'default_fallback_key_if_not_set')

# .env からメール設定を読み込む
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 465
app.config['MAIL_USE_SSL'] = True
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')
mail = Mail(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login' 
login_manager.login_message = "このページにアクセスするにはログインが必要です。"
login_manager.login_message_category = "error"

# --- 3. ユーザーモデル (Flask-Login用) ---

class User(UserMixin):
    def __init__(self, id, username, password):
        self.id = id
        self.username = username
        self.password = password

admin_user_db = {
    "1": User("1", "admin", os.environ.get('ADMIN_PASSWORD'))
}

# app.py 内の関数


@login_manager.user_loader
def load_user(user_id):
    return admin_user_db.get(user_id)

# --- 4. データベースモデル (ORM クラス) の定義 ---
# (テーブル名・列名は日本語のままとします)

class 教室(db.Model):
    __tablename__ = '教室'
    教室ID = db.Column(db.Integer, primary_key=True)
    教室名 = db.Column(db.String, nullable=False)
    授業s = db.relationship('授業', back_populates='教室')

class 学生(db.Model):
    __tablename__ = '学生'
    学生ID = db.Column(db.Integer, primary_key=True)
    学生名 = db.Column(db.String, nullable=False, unique=True)
    出席記録s = db.relationship('出席記録', back_populates='学生', cascade="all, delete-orphan")
    在室履歴s = db.relationship('在室履歴', back_populates='学生', cascade="all, delete-orphan")

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
    開始時刻 = db.Column(SQLTime, nullable=False) # ⬅️ SQLTime に変更
    終了時刻 = db.Column(SQLTime, nullable=False) # ⬅️ SQLTime に変更
    備考 = db.Column(db.String)

class 授業計画(db.Model):
    __tablename__ = '授業計画'
    日付 = db.Column(db.String, primary_key=True) # YYYY/MM/DD
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
    # 以前のスキーマ (PRAGMAの結果) に合わせ、PKはROWIDに依存 (autoincrement=True)
    ID = db.Column(db.Integer, primary_key=True, autoincrement=True)
    学生ID = db.Column(db.Integer, db.ForeignKey('学生.学生ID', ondelete='CASCADE'), nullable=False)
    授業ID = db.Column(db.Integer, db.ForeignKey('授業.授業ID', ondelete='CASCADE'), nullable=False)
    出席時刻 = db.Column(SQLDateTime, nullable=False, default=datetime.now)
    状態 = db.Column(db.String, nullable=False) 
    時限 = db.Column(db.Integer, nullable=False)
    
    出席日付 = Column(SQLDateTime, Computed(func.date(出席時刻)))

    学生 = db.relationship('学生', back_populates='出席記録s')
    授業 = db.relationship('授業', back_populates='出席記録s')
    
    # データベース側で日付を抽出する関数 func.date() を使用
    # これにより SQLite と PostgreSQL の両方で動作
    __table_args__ = (
        UniqueConstraint('学生ID', '授業ID', '時限', '出席日付', name='_student_class_period_date_uc'),
    )

class 在室履歴(db.Model):
    __tablename__ = '在室履歴'
    履歴ID = db.Column(db.Integer, primary_key=True, autoincrement=True)
    学生ID = db.Column(db.Integer, db.ForeignKey('学生.学生ID', ondelete='CASCADE'), nullable=False)
    教室ID = db.Column(db.Integer, db.ForeignKey('教室.教室ID'))
    入室時刻 = db.Column(SQLDateTime, nullable=False, default=datetime.now) # ⬅️ SQLDateTime に変更
    退室時刻 = db.Column(SQLDateTime, nullable=True) # ⬅️ SQLDateTime に変更
    
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

@app.cli.command('init-db')
def init_db_command():
    """Flask CLIコマンド: flask init-db"""
    print("データベーステーブルを作成中...")
    db.create_all()
    print("データベース初期化完了。")
    
    # --- データ投入ロジック ---
    from datetime import time 
    from sqlalchemy.exc import IntegrityError
    
    with app.app_context():
        
        # --- 1. 教室 (Rooms) ---
        if 教室.query.count() == 0:
            print("教室に初期データを挿入中...")
            try:
            # 提供されたデータを行ごとにパース
                room_data = [
                    (999, '教室不明'), (1205, 'A205'), (2102, 'B102/103'), (2201, 'B201'),
                    (2202, 'B202'), (2204, 'B204'), (2205, 'B205'), (2301, 'B301'),
                    (2302, 'B302'), (2303, 'B303'), (2304, 'B304'), (2305, 'B305'),
                    (2306, 'B306(視聴覚室)'), (3101, 'C101(生産ロボット室)'), (3103, 'C103(開発課題実習室)'),
                    (3201, 'C201'), (3202, 'C202(応用課程計測制御応用実習室)'), (3203, 'C203'), (3204, 'C204'),
                    (3231, 'C231(資料室)'), (3301, 'C301(マルチメディア実習室)'), (3302, 'C302(システム開発実習室)'),
                    (3303, 'C303(システム開発実習室Ⅱ)'), (3304, 'C304/305(応用課程生産管理ネットワーク応用実習室)'),
                    (3306, 'C306(共通実習室)'), (4102, 'D102(回路基板加工室)'), (4201, 'D201(開発課題実習室)'),
                    (4202, 'D202(電子情報技術科教官室)'), (4231, 'D231(準備室)'), (4301, 'D301'),
                    (4302, 'D302(PC実習室)')
                ]
                initial_rooms = [教室(教室ID=rid, 教室名=rname) for rid, rname in room_data]
                db.session.add_all(initial_rooms)
                db.session.commit()
                print("教室の初期データ挿入完了。")
            except Exception as e:
                    db.session.rollback()
                    print(f"FATAL INSERT ERROR (教室): {e}")    
            
        # --- 2. 授業科目 (Subjects) ---
        if 授業.query.count() == 0:
            print("授業に初期データを挿入中...")
            try:
                subject_data = [
                    (301, '工業技術英語', 'ワット', 2201), (302, '生産管理', '佐藤先生', None), (303, '品質管理', '田中先生', None),
                    (304, '経営管理', None, None), (305, '創造的開発技法', None, None), (306, '工業法規', None, None),
                    (307, '職業能力開発体系論', None, None), (308, '機械工学概論', '上野', 2301), (309, 'アナログ回路応用設計技術', '諏訪原', 3301),
                    (310, 'ディジタル回路応用設計技術', '岡田', 3301), (311, '複合電子回路応用設計技術', '近藤', 3301), (312, 'ロボット工学', '杉原', 3101),
                    (313, '通信プロトコル実装設計', '中山', 3301), (314, 'セキュアシステム設計', '寺内', 3301), (315, '組込システム設計', '下泉', 3302),
                    (316, '安全衛生管理', None, None), (317, '機械工作・組立実習', '生産機械', 3302), (318, '実装設計製作実習', '近藤', 3302),
                    (319, 'EMC応用実習', None, None), (320, '電子回路設計製作応用実習', '諏訪原', 3302), (321, '制御回路設計製作実習', '玉井', 3302),
                    (322, 'センシングシステム構築実習', '玉井', 3302), (323, 'ロボット工学実習', '杉原', 3301), (324, '通信プロトコル実装実習', '中山', 3302),
                    (325, 'セキュアシステム構築実習', '寺内', 3301), (326, '生産管理システム構築実習Ⅰ', '中山', 3301), (327, '生産管理システム構築実習Ⅱ', '中山', 3301),
                    (328, '組込システム構築実習', '下泉', 3302), (329, '組込デバイス設計実習', '岡田', 3302), (330, '組込システム構築課題実習', None, None),
                    (331, '電子通信機器設計制作課題実習', None, None), (332, 'ロボット機器制作課題実習(電子情報)', None, None), (333, 'ロボット機器運用課題実習(電子情報)', None, None),
                    (334, '電子装置設計製作応用課題実習', None, None), (335, '組込システム応用課題実習', None, None), (336, '通信システム応用課題実習', None, None),
                    (337, 'ロボットシステム応用課題実習', None, None), (380, '標準課題Ⅰ', None, 3301), (381, '標準課題Ⅱ', '全員', None),
                    (390, '開発課題', None, None),
                    (0, '--- 授業なし/空欄 ---', None, None) # 授業ID=0 は時間割の空きコマ用に必須
                ]
                
                initial_subjects = [授業(授業ID=sid, 授業科目名=sname, 担当教員=teacher or None, 教室ID=room or None) 
                                    for sid, sname, teacher, room in subject_data]
                db.session.add_all(initial_subjects)
                db.session.commit()
                print("授業の初期データ挿入完了。")
            except Exception as e:
                db.session.rollback()
                print(f"FATAL INSERT ERROR (授業): {e}")

        # --- 3. 学生 (Students) ---
        if 学生.query.count() == 0:
            print("学生に初期データを挿入中...")
            try:
                student_data = [
                    (222521301, '青井 渓一郎'), (222521302, '赤坂 龍成'), (222521303, '秋好 拓海'), (222521304, '伊川 翔'),
                    (222521305, '岩切 亮太'), (222521306, '上田 和輝'), (222521307, '江本 龍之介'), (222521308, '大久保 碧瀧'),
                    (222521309, '加來 涼雅'), (222521310, '梶原 悠平'), (222521311, '管野 友富紀'), (222521312, '髙口 翔真'),
                    (222521313, '古城 静雅'), (222521314, '小柳 知也'), (222521315, '酒元 翼'), (222521316, '座光寺 孝彦'),
                    (222521317, '佐野 勇太'), (222521318, '清水 健心'), (222521319, '新谷 雄飛'), (222521320, '関原 響樹'),
                    (222521321, '髙橋 優人'), (222521322, '武富 義樹'), (222521323, '内藤 俊介'), (222521324, '野田 千尋'),
                    (222521325, '野中 雄学'), (222521326, '東 奈月'), (222521327, '古田 雅也'), (222521328, '牧野 倭大'),
                    (222521329, '松隈 駿介'), (222521330, '宮岡 嘉熙')
                ]
                initial_students = [学生(学生ID=sid, 学生名=sname) for sid, sname in student_data]
                db.session.add_all(initial_students)
                db.session.commit()
                print("学生の初期データ挿入完了。")
            except Exception as e:
                db.session.rollback()
                print(f"FATAL INSERT ERROR (学生): {e}")

        # --- 4. TimeTable (時限の時間) ---
        if TimeTable.query.count() == 0:
            print("TimeTableに初期データを挿入中...")
            try:
                period_data = [
                    (1, time(8, 50), time(10, 30), '1限目'),
                    (2, time(10, 35), time(12, 15), '2限目'),
                    (3, time(13, 0), time(14, 40), '3限目'),
                    (4, time(14, 45), time(16, 25), '4限目'),
                    (5, time(16, 40), time(18, 20), '5限目'),
                ]
                initial_periods = [TimeTable(時限=p, 開始時刻=s, 終了時刻=e, 備考=r) for p, s, e, r in period_data]
                db.session.add_all(initial_periods)
                db.session.commit()
                print("TimeTableの初期データ挿入完了。")
            except Exception as e:
                db.session.rollback()
                print(f"FATAL INSERT ERROR (Timetable): {e}")
            
        # --- 5. 時間割 (Schedule) ---
        if 時間割.query.count() == 0:
            print("時間割に初期データを挿入中...")
            schedule_data = [
                (1, '月', 1, 325, None), (1, '月', 2, 325, None), (1, '月', 4, 313, None), (1, '火', 1, 314, None),
                (1, '火', 2, 309, None), (1, '火', 3, 310, None), (1, '火', 4, 311, None), (1, '水', 1, 312, None),
                (1, '水', 2, 312, None), (1, '木', 1, 315, None), (1, '木', 2, 328, None), (1, '木', 3, 322, None),
                (1, '木', 4, 322, None), (1, '金', 1, 315, None), (1, '金', 2, 328, None), (1, '金', 3, 318, None),
                (1, '金', 4, 318, None), (2, '月', 1, 325, None), (2, '月', 2, 325, None), (2, '月', 3, 301, None),
                (2, '月', 4, 313, None), (2, '火', 1, 325, None), (2, '火', 2, 309, None), (2, '火', 3, 310, None),
                (2, '火', 4, 311, None), (2, '水', 1, 324, None), (2, '水', 2, 324, None), (2, '木', 1, 323, None),
                (2, '木', 2, 323, None), (2, '木', 3, 315, None), (2, '木', 4, 328, None), (2, '金', 1, 315, None),
                (2, '金', 2, 328, None), (2, '金', 3, 322, None), (2, '金', 4, 322, None), (3, '月', 1, 327, None),
                (3, '月', 2, 327, None), (3, '月', 3, 380, None), (3, '月', 4, 380, None), (3, '火', 1, 317, None),
                (3, '火', 2, 317, None), (3, '火', 3, 380, None), (3, '火', 4, 380, None), (3, '水', 1, 329, None),
                (3, '水', 2, 329, None), (3, '水', 3, 308, None), (3, '木', 1, 380, None), (3, '木', 2, 380, None),
                (3, '木', 3, 380, None), (3, '木', 4, 380, None), (3, '金', 1, 321, None), (3, '金', 2, 321, None),
                (3, '金', 3, 380, None), (3, '金', 4, 380, None), (4, '月', 1, 381, None), (4, '月', 2, 381, None),
                (4, '火', 1, 317, None), (4, '火', 2, 317, None), (4, '火', 3, 381, None), (4, '火', 4, 381, None),
                (4, '水', 1, 329, None), (4, '水', 2, 329, None), (4, '水', 3, 308, None), (4, '木', 1, 331, None),
                (4, '木', 2, 331, None), (4, '木', 3, 331, None), (4, '木', 4, 331, None), (4, '金', 1, 331, None),
                (4, '金', 2, 331, None), (1, '月', 5, 0, 'なし'), (1, '火', 5, 0, 'なし'), (1, '水', 5, 0, 'なし'),
                (1, '木', 5, 0, 'なし'), (1, '金', 5, 0, 'なし'), (1, '月', 3, 301, None), (3, '月', 5, 0, 'なし')
            ]
            # 曜日コードを変換: 月(1) -> '月', 火(2) -> '火' のように、TimeTableのデータから曜日を決定
            # 時間割のデータには曜日が文字列で入っているため、そのまま使用
            
            initial_schedule = []
            for row in schedule_data:
                if len(row) == 6:
                    # IDが含まれる場合（元のSQLite形式）
                    # IDは不要なので、残りの5つの値を取得
                    _, kiki, yobi, jigen, shid, biko = row
                elif len(row) == 5:
                    # IDが含まれない場合（修正後のデータ形式）
                    kiki, yobi, jigen, shid, biko = row
                else:
                    print(f"FATAL INSERT ERROR (時間割): データ形式が不正です: {row}")
                    continue # この行をスキップして次へ
                initial_schedule.append(
                    時間割(学期=str(kiki), 曜日=yobi, 時限=jigen, 授業ID=shid, 備考=biko)
                )

            try:
                db.session.add_all(initial_schedule)
                db.session.commit()
                print("時間割の初期データ挿入完了。")
            except IntegrityError as e:
                db.session.rollback()
                print(f"警告: 時間割のデータ挿入中に重複エラーが発生しました (UniqueConstraint)。スキップします。エラー: {e}")
            except Exception as e:
                db.session.rollback()
                # その他の予期せぬエラーも出力
                print(f"FATAL INSERT ERROR (時間割 - Other): {e}")

        # --- 6. 授業計画 (Lesson Plan) ---
        if 授業計画.query.count() == 0:
            print("授業計画に初期データを挿入中...")
            try:
                plan_data = [
                    ('2025/4/8', 1, 2, None), ('2025/4/9', 1, 3, None), ('2025/4/10', 1, 4, None), ('2025/4/11', 1, 5, None),
                    ('2025/4/14', 1, 1, None), ('2025/4/15', 1, 2, None), ('2025/4/16', 1, 3, None), ('2025/4/17', 1, 4, None),
                    ('2025/4/18', 1, 5, None), ('2025/4/21', 1, 1, None), ('2025/4/22', 1, 2, None), ('2025/4/23', 1, 3, None),
                    ('2025/4/24', 1, 4, None), ('2025/4/25', 1, 5, None), ('2025/4/28', 1, 1, None), ('2025/5/7', 1, 3, None),
                    ('2025/5/8', 1, 4, None), ('2025/5/9', 1, 5, None), ('2025/5/12', 1, 1, None), ('2025/5/13', 1, 2, None),
                    ('2025/5/14', 1, 3, None), ('2025/5/15', 1, 4, None), ('2025/5/16', 1, 5, None), ('2025/5/19', 1, 1, None),
                    ('2025/5/20', 1, 2, None), ('2025/5/21', 1, 3, None), ('2025/5/22', 1, 4, None), ('2025/5/23', 1, 5, None),
                    ('2025/5/26', 1, 1, None), ('2025/5/27', 1, 2, None), ('2025/5/28', 1, 3, None), ('2025/5/29', 1, 4, None),
                    ('2025/5/30', 1, 5, None), ('2025/6/2', 1, 1, None), ('2025/6/3', 1, 2, None), ('2025/6/4', 1, 3, None),
                    ('2025/6/5', 1, 4, None), ('2025/6/6', 1, 5, None), ('2025/6/9', 1, 1, None), ('2025/6/10', 1, 2, None),
                    ('2025/6/11', 1, 3, None), ('2025/6/12', 1, 4, None), ('2025/6/13', 1, 5, None), ('2025/6/16', 1, 1, None),
                    ('2025/6/17', 1, 2, None), ('2025/6/19', 1, 4, None), ('2025/6/20', 2, 5, None), ('2025/6/23', 2, 1, None),
                    ('2025/6/24', 2, 2, None), ('2025/6/25', 2, 3, None), ('2025/6/26', 2, 4, None), ('2025/6/27', 2, 5, None),
                    ('2025/6/30', 2, 1, None), ('2025/7/1', 2, 2, None), ('2025/7/2', 2, 3, None), ('2025/7/3', 2, 4, None),
                    ('2025/7/4', 2, 5, None), ('2025/7/7', 2, 1, None), ('2025/7/8', 2, 2, None), ('2025/7/9', 2, 3, None),
                    ('2025/7/10', 2, 4, None), ('2025/7/11', 2, 5, None), ('2025/8/20', 2, 2, None), ('2025/8/21', 2, 3, None),
                    ('2025/8/22', 2, 4, None), ('2025/8/23', 2, 5, None), ('2025/8/26', 2, 1, None), ('2025/8/27', 2, 2, None),
                    ('2025/8/28', 2, 3, None), ('2025/8/29', 2, 4, None), ('2025/8/30', 2, 5, None), ('2025/9/1', 2, 1, None),
                    ('2025/9/2', 2, 2, None), ('2025/9/3', 2, 3, None), ('2025/9/4', 2, 4, None), ('2025/9/5', 2, 5, None),
                    ('2025/9/8', 2, 1, None), ('2025/9/9', 2, 2, None), ('2025/9/10', 2, 3, None), ('2025/9/11', 2, 4, None),
                    ('2025/9/12', 2, 5, None), ('2025/9/16', 2, 2, None), ('2025/9/17', 2, 3, None), ('2025/9/18', 2, 4, None),
                    ('2025/9/19', 2, 5, None), ('2025/9/22', 2, 1, None), ('2025/9/24', 2, 3, None), ('2025/9/25', 2, 4, None),
                    ('2025/9/26', 2, 5, None), ('2025/9/29', 2, 1, None), ('2025/9/30', 2, 2, None), ('2025/10/1', 3, 3, None),
                    ('2025/10/2', 3, 4, None), ('2025/10/3', 3, 5, None), ('2025/10/6', 3, 1, None), ('2025/10/7', 3, 2, None),
                    ('2025/10/8', 3, 3, None), ('2025/10/9', 3, 4, None), ('2025/10/10', 3, 5, None), ('2025/10/14', 3, 2, None),
                    ('2025/10/15', 3, 3, None), ('2025/10/16', 3, 4, None), ('2025/10/17', 3, 5, None), ('2025/10/20', 3, 1, None),
                    ('2025/10/21', 3, 2, None), ('2025/10/22', 3, 3, None), ('2025/10/23', 3, 4, None), ('2025/10/24', 3, 5, None),
                    ('2025/10/27', 3, 1, None), ('2025/10/28', 3, 2, None), ('2025/10/29', 3, 3, None), ('2025/10/30', 3, 4, None),
                    ('2025/10/31', 3, 5, None), ('2025/11/4', 3, 2, None), ('2025/11/5', 3, 3, None), ('2025/11/6', 3, 4, None),
                    ('2025/11/7', 3, 5, None), ('2025/11/10', 3, 1, None), ('2025/11/11', 3, 2, None), ('2025/11/12', 3, 3, None),
                    ('2025/11/13', 3, 4, None), ('2025/11/14', 3, 5, None), ('2025/11/17', 3, 1, None), ('2025/11/18', 3, 2, None),
                    ('2025/11/19', 3, 3, None), ('2025/11/20', 3, 4, None), ('2025/11/21', 3, 5, None), ('2025/11/25', 3, 1, None),
                    ('2025/11/26', 3, 3, None), ('2025/11/27', 3, 4, None), ('2025/11/28', 3, 5, None), ('2025/12/1', 3, 1, None),
                    ('2025/12/2', 3, 2, None), ('2025/12/3', 3, 3, None), ('2025/12/4', 3, 4, None), ('2025/12/8', 3, 1, None),
                    ('2025/12/9', 3, 2, None), ('2025/12/10', 3, 3, None), ('2025/12/11', 3, 4, None), ('2025/12/12', 3, 5, None),
                    ('2025/12/15', 3, 1, None), ('2025/12/16', 3, 2, None), ('2025/12/17', 3, 3, None), ('2025/12/18', 4, 4, None),
                    ('2025/12/19', 4, 5, None), ('2025/12/22', 4, 1, None), ('2025/12/23', 4, 2, None), ('2025/12/24', 4, 3, None),
                    ('2025/12/25', 4, 4, None), ('2025/12/26', 4, 5, None), ('2026/1/7', 4, 3, None), ('2026/1/8', 4, 4, None),
                    ('2026/1/9', 4, 5, None), ('2026/1/12', 4, 1, None), ('2026/1/13', 4, 2, None), ('2026/1/14', 4, 3, None),
                    ('2026/1/15', 4, 4, None), ('2026/1/16', 4, 5, None), ('2026/1/19', 4, 1, None), ('2026/1/20', 4, 2, None),
                    ('2026/1/21', 4, 3, None), ('2026/1/22', 4, 4, None), ('2026/1/23', 4, 5, None), ('2026/1/26', 4, 1, None),
                    ('2026/1/27', 4, 2, None), ('2026/1/28', 4, 3, None), ('2026/1/29', 4, 4, None), ('2026/1/30', 4, 5, None),
                    ('2026/2/2', 4, 1, None), ('2026/2/3', 4, 2, None), ('2026/2/4', 4, 3, None), ('2026/2/5', 4, 4, None),
                    ('2026/2/6', 4, 5, None), ('2026/2/9', 4, 1, None), ('2026/2/10', 4, 2, None), ('2026/2/12', 4, 4, None),
                    ('2026/2/13', 4, 5, None), ('2026/2/16', 4, 1, None), ('2026/2/17', 4, 2, None), ('2026/2/18', 4, 3, None),
                    ('2026/2/19', 4, 4, None), ('2026/2/20', 4, 5, None), ('2026/2/23', 4, 1, None), ('2026/2/24', 4, 2, None),
                    ('2026/2/25', 4, 3, None), ('2026/2/26', 4, 4, None), ('2026/2/27', 4, 5, None), ('2026/3/2', 4, 1, None),
                    ('2026/3/3', 4, 2, None)
                ]
                initial_plan = [授業計画(日付=d, 期=k, 授業曜日=y, 備考=r) for d, k, y, r in plan_data]
                db.session.add_all(initial_plan)
                db.session.commit()
                print("授業計画の初期データ挿入完了。")
            except Exception as e:
                db.session.rollback()
                print(f"FATAL INSERT ERROR (授業計画): {e}")
            
        # --- 7. デフォルト時間割のバックアップ ---
        initialize_default_schedule()
        
    # --- データベース初期化完了 ---

# --- 5. ヘルパー関数 (SQLAlchemy版) ---

YOBI_MAP = {'月': 1, '火': 2, '水': 3, '木': 4, '金': 5, '土': 6, '日': 0}
ROMAN_TO_INT = {'Ⅰ': 1, 'Ⅱ': 2, 'Ⅲ': 3, 'Ⅳ': 4}
YOBI_MAP_REVERSE = {v: k for k, v in YOBI_MAP.items()}

def get_current_kiki():
    now = datetime.now()
    today_str = f"{now.year}/{now.month}/{now.day}"
    result = 授業計画.query.filter_by(日付=today_str).first()
    return str(result.期) if result else "1"

def initialize_default_schedule():
    """DB初期化コマンド (Flask CLI) から実行する"""
    try:
        with app.app_context():
            db.create_all()
            
            with db.engine.connect() as conn:
                result = conn.execute(text("SELECT COUNT(*) FROM \"時間割_デフォルト\"")).fetchone()
                if result[0] == 0:
                    conn.execute(text("INSERT INTO \"時間割_デフォルト\" (\"学期\", \"曜日\", \"時限\", \"授業ID\", \"備考\") SELECT \"学期\", \"曜日\", \"時限\", \"授業ID\", \"備考\" FROM \"時間割\""))
                    conn.commit()
                    print("【INFO】デフォルト時間割のバックアップが作成されました。")
    except Exception as e:
        print(f"【WARNING】デフォルト時間割の初期化に失敗しました: {e}")

def get_schedule_for_line(target_date):
    """指定された日付の時間割をテキスト形式で返す"""
    
    # 曜日コード (0=月, 1=火, ...)
    yobi_code = target_date.weekday() 
    yobi_str = YOBI_MAP_REVERSE.get(yobi_code)
    
    # 授業計画から期と曜日コードを取得
    date_str = target_date.strftime("%Y/%m/%d")
    plan_row = 授業計画.query.get(date_str)
    
    if plan_row:
        kiki = str(plan_row.期)
        yobi_to_use = YOBI_MAP_REVERSE.get(plan_row.授業曜日) # 授業計画に指定された曜日コード
    else:
        kiki = get_current_kiki() # 授業計画がない場合は現在の期を使用
        yobi_to_use = yobi_str
    
    # 時間割データの取得
    schedule_rows = db.session.query(
        時間割, 授業.授業科目名, 授業.担当教員
    ).outerjoin(授業, 時間割.授業ID == 授業.授業ID)\
     .filter(時間割.学期 == kiki, 時間割.曜日 == yobi_to_use)\
     .order_by(時間割.時限).all()
     
    if not schedule_rows:
        return f"{date_str} ({yobi_str}): 授業計画が見つからないか、休校日です。"

    output = [f"📅 {date_str} ({yobi_to_use}) - 第{kiki}期 の時間割"]
    for row in schedule_rows:
        time_row = TimeTable.query.get(row[0].時限) # TimeTableにアクセス
        time_str = f"({time_row.開始時刻.strftime('%H:%M')}-{time_row.終了時刻.strftime('%H:%M')})" if time_row else ""
        
        subject_name = row[1] if row[1] else (row[0].備考 if row[0].備考 else "空き時間")
        teacher = row[2] if row[2] else ""

        output.append(f"  {row[0].時限}限 {time_str}\n  {subject_name} {teacher}")

    return "\n".join(output)

def get_attendance_summary_for_line(line_user_id):
    """LINEユーザーIDに対応する学生の出席サマリーを返す (簡略版)"""
    # ⚠️ データベースにLINEユーザーIDと学生IDの紐付けテーブルが必要です。
    # 現在のモデルに紐付けテーブルがないため、ここでは仮に学生ID=222521301のデータを使用します。
    # 実際には、学生が最初にBotを利用する際にIDを登録する機能が必要です。
    
    student_id = 222521301 # デバッグ用の仮の学生ID
    
    # ... (レポート機能のロジックを流用し、学生IDで出席記録を集計してテキストで返す)
    # ... (ここでは、詳細な集計ロジックは省略し、簡潔なメッセージを返します)
    
    return f"【学生ID:{student_id}】の出席サマリー:\n詳細なレポートはWeb管理画面を参照してください。"

def process_exit_record(line_user_id):
    """学生の在室履歴を終了させる"""
    # ⚠️ ここでもLINEユーザーIDと学生IDの紐付けが必要です。
    student_id = 222521301 
    
    existing_session = 在室履歴.query.filter_by(学生ID=student_id, 退室時刻=None).first()
    
    if existing_session:
        existing_session.退室時刻 = datetime.now()
        db.session.commit()
        return f"✅ {existing_session.学生.学生名}さんの退室時刻を記録しました。"
    else:
        return "⚠️ 現在、入室記録が見つかりませんでした。"

def 判定(時限, 登録時刻):
    row = TimeTable.query.get(時限)
    if not row: return "未定義"
    
    # row.開始時刻 は datetime.time オブジェクト
    開始 = datetime.combine(登録時刻.date(), row.開始時刻)
    経過 = (登録時刻 - 開始).total_seconds() / 60
    
    if 経過 <= 0: return "出席"
    elif 経過 <= 20: return "遅刻"
    else: return "欠席"

sensor_data = [] # センサーデータ（ESP32）用

# --- 6. 認証ルート (Login / Logout) ---
@app.route("/login", methods=["GET", "POST"])
def login():
    """ログインページと認証処理"""
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        
        user_to_login = None
        for user_id, user_obj in admin_user_db.items():
            if user_obj.username == username:
                user_to_login = user_obj
                break
        
        if user_to_login and user_to_login.password == password:
            login_user(user_to_login) # ログイン状態をセッションに保存
            flash("✅ ログインしました。", "success")
            return redirect(url_for('index'))
        else:
            flash("❌ ユーザー名またはパスワードが間違っています。", "error")
            return redirect(url_for('login'))
    
    # GETリクエストの場合はログインページを表示
    return render_template("login.html")


@app.route("/logout")
@login_required  # ログインしている人だけがログアウトできる
def logout():
    """ログアウト処理"""
    logout_user() # セッションからユーザー情報を削除
    flash("✅ ログアウトしました。", "info")
    return redirect(url_for('index'))

# --- 7. メインページ (ダッシュボード) ---
@app.route("/")
def index():
    students = 学生.query.order_by(学生.学生ID).all()
    
    message = None
    category = None
    messages = get_flashed_messages(with_categories=True)
    if messages:
        category, message = messages[0]
            
    links = [
        {"url": "/attendance?kiki=1", "name": "出席登録 / 全体記録"},
        {"url": "/schedule", "name": "時間割表示"},
        {"url": "/edit_schedule?kiki=1", "name": "時間割編集"},
        {"url": "/manage_students", "name": "学生管理"},
        {"url": "/manage_subjects", "name": "授業科目管理"},
    ]
    
    return render_template("index.html", 
                           links=links, 
                           students=[(s.学生ID, s.学生名) for s in students], # テンプレートが (id, name) を期待
                           message=message,
                           category=category)

# --- 8. APIルート (SQLAlchemy版) ---

@app.route("/api/register_attendance", methods=["POST"])
@login_required 
def api_register_attendance():
    """(顔認証API) 出席信号を受け取りDBに登録 (ORM版)"""
    try:
        data = request.get_json()
        if not data or "student_id" not in data:
            return jsonify({"error": "学生ID (student_id) が必要です。"}), 400
            
        student_id = data.get("student_id")
        now = datetime.now()
        
        # --- 1. 授業IDと時限を特定 ---
        today_str = f"{now.year}/{now.month}/{now.day}"
        
        plan_row = 授業計画.query.get(today_str)
        if not plan_row: return jsonify({"error": "今日は授業計画にない日です。"}), 200 
        
        kiki, yobi_code = plan_row.期, plan_row.授業曜日
        
        period_row = TimeTable.query.filter(
            TimeTable.開始時刻 <= now.time(),
            TimeTable.終了時刻 >= now.time()
        ).first()
        if not period_row: return jsonify({"error": "現在は授業時間外です。"}), 200
            
        current_period = period_row.時限
        yobi_str = YOBI_MAP_REVERSE.get(yobi_code)
        
        class_row = 時間割.query.filter_by(
            学期=str(kiki), 曜日=yobi_str, 時限=current_period
        ).first()
        
        if not class_row or class_row.授業ID == 0:
            return jsonify({"error": "この時間割スロットに授業がありません。"}), 200
            
        class_id = class_row.授業ID

        # --- 2. 出席状態を判定し、DBに登録 ---
        status = 判定(current_period, now)
        
        try:
            new_attendance = 出席記録(
                学生ID=student_id,
                授業ID=class_id,
                出席時刻=now,
                状態=status,
                時限=current_period
            )
            db.session.add(new_attendance)
            db.session.commit()
        
        except IntegrityError: # (UniqueConstraint 違反)
            db.session.rollback()
            print(f"INFO (API): 出席記録は登録済み (UniqueConstraint)。在室履歴をチェックします。")
        
        # --- 在室履歴ロジック (ORM) ---
        room = 授業.query.get(class_id)
        room_id = room.教室ID if room and room.教室ID is not None else 999 

        existing_session = 在室履歴.query.filter_by(学生ID=student_id, 退室時刻=None).first()
        
        if not existing_session:
            new_session = 在室履歴(学生ID=student_id, 教室ID=room_id, 入室時刻=now, 退室時刻=None)
            db.session.add(new_session)
            db.session.commit()
        
        if status == "欠席":
            return jsonify({"success": True, "message": f"学生 {student_id} は「欠席」ですが、「在室」として記録しました。"}), 201
        else:
            return jsonify({"success": True, "message": f"学生 {student_id} を {status} として登録しました。"}), 201

    except Exception as e:
        db.session.rollback()
        print(f"FATAL API ERROR (api_register_attendance): {e}")
        return jsonify({"error": f"サーバー内部エラー: {e}"}), 500

@app.route("/api/update_status", methods=["POST"])
@login_required
def api_update_status():
    """(事後修正API) 出席記録のステータスを更新する (ORM版)"""
    data = request.get_json()
    record_rowid = data.get("record_id") # HTMLはROWID (PK) を送ってくる
    new_status = data.get("new_status")

    if not record_rowid or not new_status:
        return jsonify({"error": "IDと新しいステータスが必要です。"}), 400

    try:
        record_to_update = 出席記録.query.get(record_rowid)
        
        if record_to_update:
            record_to_update.状態 = new_status
            db.session.commit()
            return jsonify({"success": True, "message": f"記録ID {record_rowid} を {new_status} に更新しました。"}), 200
        else:
             return jsonify({"error": "該当する記録IDが見つかりません。"}), 404

    except Exception as e:
        db.session.rollback()
        print(f"FATAL API ERROR (update_status): {e}")
        return jsonify({"error": f"サーバー内部エラー: {e}"}), 500

@app.route("/api/status")
def api_status():
    """(ダッシュボードAPI) リアルタイム在室状況を返す (ORM版)"""
    
    all_students = 学生.query.all()
    
    # 教室名も取得 (LEFT JOIN)
    active_sessions = db.session.query(
        在室履歴.学生ID, 教室.教室名, 在室履歴.入室時刻
    ).outerjoin(教室, 在室履歴.教室ID == 教室.教室ID)\
     .filter(在室履歴.退室時刻 == None).all()

    now = datetime.now()
    active_map = {}
    for sid, room_name, 入室時刻 in active_sessions:
        try:
            滞在秒 = int((now - 入室時刻).total_seconds())
            hh = 滞在秒 // 3600
            mm = (滞在秒 % 3600) // 60
            ss = 滞在秒 % 60
            duration = f"{hh:02}:{mm:02}:{ss:02}"
            active_map[sid] = (room_name or '教室不明', 入室時刻.strftime("%Y-%m-%d %H:%M:%S"), duration)
        except ValueError:
             active_map[sid] = (room_name or '教室不明', 入室時刻.strftime("%Y-%m-%d %H:%M:%S"), "Error")

    result = []
    for s in all_students:
        if s.学生ID in active_map:
            room, time, dur = active_map.pop(s.学生ID) # 重複表示防止
            result.append({
                "name": s.学生名, "status": "在室",
                "room": room, "entry": time, "duration": dur
            })
        else:
            result.append({
                "name": s.学生名, "status": "退出",
                "room": "", "entry": "", "duration": ""
            })

    return jsonify({"students": result})

@app.route("/api/sensor", methods=["POST"])
def receive_sensor():
    """(ESP32 API) センサーデータ受信"""
    data = request.get_json()
    if data:
        entry = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "temperature": data.get("temperature"),
            "humidity": data.get("humidity")
        }
        sensor_data.append(entry)
        # print("ESP32から受信:", entry) # デバッグ用
        return jsonify({"status": "ok"}), 200
    return jsonify({"error": "Invalid data"}), 400

@app.route("/api/sensor_status")
def api_sensor_status():
    """(ダッシュボードAPI) センサーデータ取得"""
    if sensor_data:
        latest = sensor_data[-1]
        return jsonify(latest)
    else:
        return jsonify({}) # データがない場合は空のJSONを返す

# --- 9. 管理ページ (SQLAlchemy版) ---

@app.route("/attendance", methods=["GET", "POST"])
@login_required
def attendance():
    """(手動登録) 出席登録フォームと全体記録 (ORM版)"""
    selected_kiki = request.args.get("kiki", get_current_kiki())

    if request.method == "POST":
        try:
            学生ID = int(request.form.get("student_id"))
            授業ID = int(request.form.get("class_id"))
            時限 = int(request.form.get("period"))
            登録時刻 = datetime.now()
            状態 = 判定(時限, 登録時刻)

            try:
                new_attendance = 出席記録(
                    学生ID=学生ID,
                    授業ID=授業ID,
                    出席時刻=登録時刻,
                    状態=状態,
                    時限=時限
                )
                db.session.add(new_attendance)
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                print(f"INFO (Manual): 出席記録は登録済み (UniqueConstraint)。在室履歴をチェックします。")

            # (在室履歴ロジック - 統一版)
            room = 授業.query.get(授業ID)
            教室ID = room.教室ID if room and room.教室ID is not None else 999 

            existing_session = 在室履歴.query.filter_by(学生ID=学生ID, 退室時刻=None).first()
            if not existing_session:
                new_session = 在室履歴(学生ID=学生ID, 教室ID=教室ID, 入室時刻=登録時刻, 退室時刻=None)
                db.session.add(new_session)
                db.session.commit()
        
        except Exception as e:
            db.session.rollback()
            print(f"FATAL ERROR (Manual Attendance): {e}")
        
        return redirect(f"/attendance?kiki={selected_kiki}")

    # --- GETリクエスト時の表示データ取得 ---
    students = 学生.query.order_by(学生.学生ID).all()
    
    # 学期で絞り込んだ授業リスト
    classes = db.session.query(授業.授業ID, 授業.授業科目名)\
                .join(時間割, 授業.授業ID == 時間割.授業ID)\
                .filter(時間割.学期 == selected_kiki, 授業.授業ID != 0)\
                .distinct().order_by(授業.授業ID).all()
    
    # 学期で絞り込んだ出席記録 (生のSQL + text() を使用)
    sql_query = text("""
        SELECT ST."学生名", R."授業ID", R."出席時刻", R."状態", R."時限"
        FROM "出席記録" R
        JOIN "学生" ST ON R."学生ID" = ST."学生ID"
        WHERE R."授業ID" IN (
            SELECT DISTINCT T."授業ID" FROM "時間割" T WHERE T."学期" = :kiki
        )
        ORDER BY R."出席時刻" DESC
    """)
    attendance_records = db.session.execute(sql_query, {"kiki": selected_kiki}).fetchall()

    latest_sensor = sensor_data[-1] if sensor_data else None

    return render_template("attendance_combined.html",
                           students=[(s.学生ID, s.学生名) for s in students],
                           classes=classes, 
                           attendance=attendance_records, 
                           sensor=latest_sensor,
                           selected_kiki=selected_kiki, 
                           kikis=["1", "2", "3", "4"])

@app.route("/schedule")
def schedule():
    """(閲覧) 時間割表示ページ (ORM版)"""

    selected_kiki = request.args.get('kiki', default=None, type=str)
    
    if selected_kiki is None:
        # PostgreSQL互換クエリで、今日以前で日付が最も新しい授業計画の「期」を取得する
        # (日付は 'YYYY/MM/DD' 形式で、PostgreSQLのTO_DATEで比較します)
        current_kiki_record = db.session.query(授業計画.期) \
            .filter(text("TO_DATE(REPLACE(授業計画.\"日付\", '/', '-'), 'YYYY-MM-DD') <= CURRENT_DATE")) \
            .order_by(text("TO_DATE(REPLACE(授業計画.\"日付\", '/', '-'), 'YYYY-MM-DD') DESC")) \
            .first()
        
        if current_kiki_record:
            # 🚨 修正箇所 1: 整数として取得された期をここで文字列に変換
            selected_kiki = str(current_kiki_record[0]) 
        else:
            selected_kiki = '1'

    if selected_kiki is not None:
        selected_kiki = str(selected_kiki)
    else:
        # ここに来ることは稀ですが、一応のフォールバック
        selected_kiki = '1'

    # 授業と教室をJOINして取得
    schedules_rows = db.session.query(
        時間割.時間割ID, 時間割.曜日, 時間割.時限, 時間割.学期, 
        授業.授業科目名, 授業.担当教員, 教室.教室名, 
        時間割.備考, 時間割.授業ID
    ).outerjoin(授業, 時間割.授業ID == 授業.授業ID)\
     .outerjoin(教室, 授業.教室ID == 教室.教室ID)\
     .filter(時間割.学期 == selected_kiki)\
     .order_by(時間割.時限, 時間割.曜日).all()

    順序 = ["月", "火", "水", "木", "金"]
    時限一覧 = list(range(1, 6))
    schedule_grid = OrderedDict()
    
    for j in 時限一覧:
        schedule_grid[j] = {y: {"is_empty": True, "remark": None, "teacher": None, "room": None, "display_text": "休憩/空欄"} for y in 順序}
        
    for row in schedules_rows:
        時間割ID, 曜日, 時限, 学期, 授業科目名, 担当教員, 教室名, 備考, 授業ID = row 
        if 時限 in 時限一覧 and 曜日 in 順序:
            教員名 = 担当教員 if 担当教員 else '教員不明'
            表示用教室名 = 教室名 if 教室名 else '教室不明'
            display_name = 備考 if 時限 == 5 else (授業科目名 if 授業科目名 else "授業名不明")
            is_empty = (not 授業ID and not 備考) if (時限 < 5 or (時限 == 5 and not 備考)) else (時限 == 5 and not 備考)
            
            schedule_grid[時限][曜日] = {
                "id": 時間割ID, "subject": 授業科目名, "teacher": 教員名,
                "room": 表示用教室名, "display_text": display_name,
                "subject_id": 授業ID if 授業ID else 0,
                "remark": 備考, "is_empty": is_empty
            }

    today_yobi = YOBI_MAP_REVERSE.get(datetime.now().weekday())

    return render_template("schedule.html", 
                           schedule_grid=schedule_grid, 曜日順=順序, 時限一覧=時限一覧,
                           selected_kiki=selected_kiki, kikis=["1", "2", "3", "4"],
                           today_yobi=today_yobi)
    
@app.route("/edit_schedule", methods=["GET", "POST"])
@login_required
def edit_schedule():
    """(管理) 時間割編集ページ (ORM版)"""
    selected_kiki = request.args.get("kiki", get_current_kiki())
    success_message = None
    error_message = None

    if request.method == "POST":
        try:
            ターゲット学期 = request.form["kiki"]
            ターゲット曜日 = request.form["day"]
            ターゲット時限 = int(request.form["period"])
            新授業ID = request.form.get("new_subject_id")
            備考テキスト = request.form.get("remark_text")

            is_delete_action = (ターゲット時限 < 5 and 新授業ID == "0")

            existing_slot = 時間割.query.filter_by(
                学期=ターゲット学期, 曜日=ターゲット曜日, 時限=ターゲット時限
            ).first()

            if is_delete_action:
                if existing_slot:
                    db.session.delete(existing_slot)
                    db.session.commit()
                    success_message = f"✅ 第{ターゲット学期}期 {ターゲット曜日}{ターゲット時限}限 の時間割を削除しました。"
                else:
                    error_message = f"エラー: 第{ターゲット学期}期 {ターゲット曜日}{ターゲット時限}限 は元々空欄です。"
            
            else:
                final_remark = 備考テキスト if 備考テキスト and 備考テキスト.strip() else None
                
                if existing_slot:
                    if ターゲット時限 < 5:
                        existing_slot.授業ID = 新授業ID
                        existing_slot.備考 = None
                    else: # ターゲット時限 == 5
                        existing_slot.授業ID = 0
                        existing_slot.備考 = final_remark
                    success_message = f"✅ 時間割 (ID:{existing_slot.時間割ID}) が更新されました。"
                
                else: # 新規挿入
                    new_slot = 時間割(
                        学期=ターゲット学期, 曜日=ターゲット曜日, 時限=ターゲット時限,
                        授業ID = 新授業ID if ターゲット時限 < 5 else 0,
                        備考 = final_remark if ターゲット時限 == 5 else None
                    )
                    db.session.add(new_slot)
                    success_message = f"✅ 新しい時間割が登録されました。"
                
                db.session.commit()
                selected_kiki = ターゲット学期 
                
        except Exception as e:
            db.session.rollback()
            error_message = f"更新エラー: {e}"

    # --- GET/POST後の表示データ取得 ---
    subjects_query = 授業.query.order_by(授業.授業ID).all()
    subjects = [(s.授業ID, s.授業科目名) for s in subjects_query]
    subjects.insert(0, (0, "--- 授業を削除/空欄にする ---"))
    
    schedules_rows = db.session.query(
        時間割.時間割ID, 授業.授業科目名, 時間割.曜日, 時間割.時限, 
        時間割.授業ID, 時間割.学期, 時間割.備考
    ).outerjoin(授業, 時間割.授業ID == 授業.授業ID)\
     .filter(時間割.学期 == selected_kiki)\
     .order_by(時間割.時限, 時間割.曜日).all()
    
    順序 = ["月", "火", "水", "木", "金"]
    時限一覧 = list(range(1, 6))
    schedule_grid = OrderedDict()
    
    for j in 時限一覧:
        schedule_grid[j] = {y: {"時間割ID": None, "授業科目名": "休憩/空欄", "授業ID": 0, "備考": None, "is_empty": True} for y in 順序}
        
    for row in schedules_rows:
        時間割ID, 授業科目名, 曜日, 時限, 授業ID, 学期, 備考 = row
        if 時限 in 時限一覧 and 曜日 in 順序:
            display_name = 備考 if 時限 == 5 else (授業科目名 if 授業科目名 else "休憩/空欄")
            
            schedule_grid[時限][曜日] = {
                "時間割ID": 時間割ID, "授業科目名": display_name,
                "授業ID": 授業ID if 授業ID else 0, "備考": 備考,
                "is_empty": (not 授業ID and not 備考)
            }
    
    return render_template("edit_schedule.html", 
                           schedule_grid=schedule_grid, subjects=subjects, 曜日順=順序,
                           時限一覧=時限一覧, selected_kiki=selected_kiki,
                           kikis=["1", "2", "3", "4"], error=error_message, success=success_message)

@app.route("/restore_schedule", methods=["POST"])
@login_required
def restore_schedule():
    """(管理) 時間割をデフォルトに復元 (SQLAlchemy text()版)"""
    try:
        with db.engine.connect() as conn:
            conn.execute(text("DELETE FROM \"時間割\""))
            conn.execute(text("INSERT INTO \"時間割\" (\"学期\", \"曜日\", \"時限\", \"授業ID\", \"備考\") SELECT \"学期\", \"曜日\", \"時限\", \"授業ID\", \"備考\" FROM \"時間割_デフォルト\""))
            conn.commit()
        flash("✅ 時間割がデフォルトの状態に復元されました。", "success")
    except Exception as e:
        flash(f"❌ 時間割の復元中にエラーが発生しました: {e}", "error")
        
    return redirect(url_for('index'))

@app.route("/manage_students", methods=["GET", "POST"])
@login_required
def manage_students():
    """(管理) 学生の追加・削除 (ORM版)"""
    message = None
    category = None

    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "add":
                student_id = request.form.get("student_id")
                student_name = request.form.get("student_name")
                if student_id and student_name:
                    new_student = 学生(学生ID=student_id, 学生名=student_name)
                    db.session.add(new_student)
                    db.session.commit()
                    message = f"✅ 学生 '{student_name}' (ID: {student_id}) を追加しました。"
                    category = "success"
                else:
                    message = "❌ 学生IDと学生名の両方が必要です。"
                    category = "error"
            
            elif action == "delete":
                student_id = request.form.get("student_id")
                if student_id:
                    student_to_delete = 学生.query.get(student_id)
                    if student_to_delete:
                        # 関連する記録(出席記録, 在室履歴)は cascade="all, delete-orphan" により自動で削除される
                        db.session.delete(student_to_delete)
                        db.session.commit()
                        message = f"✅ 学生ID {student_id} と関連する出席記録をすべて削除しました。"
                        category = "success"
        except IntegrityError:
            db.session.rollback()
            message = f"❌ エラー: 学生ID {student_id} は既に使用されています。"
            category = "error"
        except Exception as e:
            db.session.rollback()
            message = f"❌ データベースエラー: {e}"
            category = "error"

    students = 学生.query.order_by(学生.学生ID).all()
    
    return render_template("manage_students.html", 
                           students=[(s.学生ID, s.学生名) for s in students], 
                           message=message, 
                           category=category)

@app.route("/manage_subjects", methods=["GET", "POST"])
@login_required
def manage_subjects():
    """(管理) 授業科目の追加・削除 (ORM版)"""
    message = None
    category = None

    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "add":
                subject_id = request.form.get("subject_id")
                subject_name = request.form.get("subject_name")
                teacher_name = request.form.get("teacher_name")
                room_id = request.form.get("room_id")
                room_id = room_id if room_id else None 

                if subject_id and subject_name:
                    new_subject = 授業(授業ID=subject_id, 授業科目名=subject_name, 担当教員=teacher_name, 教室ID=room_id)
                    db.session.add(new_subject)
                    db.session.commit()
                    message = f"✅ 授業 '{subject_name}' (ID: {subject_id}) を追加しました。"
                    category = "success"
                else:
                    message = "❌ 授業IDと授業科目名の両方が必要です。"
                    category = "error"
            
            elif action == "delete":
                subject_id = request.form.get("subject_id")
                if subject_id:
                    subject_to_delete = 授業.query.get(subject_id)
                    if subject_to_delete:
                        # 関連する記録(時間割, 出席記録)は cascade="all, delete-orphan" により自動で削除される
                        db.session.delete(subject_to_delete)
                        db.session.commit()
                        message = f"✅ 授業ID {subject_id} と関連する時間割・出席記録をすべて削除しました。"
                        category = "success"
        except IntegrityError:
            db.session.rollback()
            message = f"❌ エラー: 授業ID {subject_id} は既に使用されています。"
            category = "error"
        except Exception as e:
            db.session.rollback()
            message = f"❌ データベースエラー: {e}"
            category = "error"

    # 教室名もJOINして取得
    subjects = db.session.query(授業.授業ID, 授業.授業科目名, 授業.担当教員, 教室.教室名, 授業.教室ID)\
               .outerjoin(教室, 授業.教室ID == 教室.教室ID)\
               .order_by(授業.授業ID).all()
    
    rooms = 教室.query.order_by(教室.教室ID).all()
    
    return render_template("manage_subjects.html", 
                           subjects=subjects, 
                           rooms=[(r.教室ID, r.教室名) for r in rooms],
                           message=message, 
                           category=category)

# --- 10. レポート (SQLAlchemy版, text() ハイブリッド) ---

@app.route("/my_attendance", methods=["GET"])
@login_required
def my_attendance():
    """(レポート) 個人別出席サマリー (text()版)"""
    student_id = request.args.get("student_id")
    selected_kiki = request.args.get("kiki", get_current_kiki())
    
    if not student_id or not student_id.isdigit():
        flash("学生IDが指定されていません。", "error")
        return redirect(url_for('index'))
    student_id = int(student_id)
    
    student_info = 学生.query.get(student_id)
    student_name = student_info.学生名 if student_info else "不明な学生"

    # 1. 履修科目を時間割から取得
    sql_enrolled = text("""
        SELECT DISTINCT S."授業科目名", S."授業ID"
        FROM "時間割" T
        JOIN "授業" S ON T."授業ID" = S."授業ID"
        WHERE T."学期" = :kiki AND T."授業ID" != 0 
        ORDER BY S."授業科目名"
    """)
    enrolled_subjects = db.session.execute(sql_enrolled, {"kiki": selected_kiki}).fetchall()

    grouped_attendance = OrderedDict()
    
    # 2. 総授業回数を計算
    for subject_name, subject_id in enrolled_subjects:
        if subject_name not in grouped_attendance:
            grouped_attendance[subject_name] = {
                "id": subject_id, "attendance_count": 0, "tardy_count": 0,
                "absent_count": 0, "total_classes_planned": 0, "total_classes_so_far": 0, "attendance_rate": 0.0
            }
        
        sql_schedule = text('SELECT T."曜日", COUNT(T."時限") FROM "時間割" T WHERE T."授業ID" = :sid AND T."学期" = :kiki GROUP BY T."曜日"')
        schedule_data = db.session.execute(sql_schedule, {"sid": subject_id, "kiki": selected_kiki}).fetchall()
        
        total_classes_planned = 0
        total_classes_so_far = 0
        
        for day_of_week, periods_per_day in schedule_data:
            day_code = YOBI_MAP.get(day_of_week)
            total_days_planned = 0
            total_days_so_far = 0
            if day_code is not None:
                sql_days_planned = text('SELECT COUNT(*) FROM "授業計画" WHERE "期" = :kiki AND "授業曜日" = :code')
                total_days_planned = db.session.execute(sql_days_planned, {"kiki": selected_kiki, "code": day_code}).scalar()
                
                sql_days_so_far = text("""
                    SELECT COUNT("日付") FROM "授業計画" 
                    WHERE "期" = :kiki AND "授業曜日" = :code 
                    AND TO_DATE(REPLACE("日付", '/', '-'), 'YYYY-MM-DD') <= CURRENT_DATE
                """)

                total_days_so_far = db.session.execute(sql_days_so_far, {"kiki": selected_kiki, "code": day_code}).scalar()

            total_classes_planned += total_days_planned * periods_per_day
            total_classes_so_far += total_days_so_far * periods_per_day
        
        grouped_attendance[subject_name]["total_classes_planned"] = total_classes_planned
        grouped_attendance[subject_name]["total_classes_so_far"] = total_classes_so_far

    # 3. 出席記録を集計
    sql_records = text("""
        SELECT R."状態", S."授業科目名"
        FROM "出席記録" R
        JOIN "授業" S ON R."授業ID" = S."授業ID"
        WHERE R."学生ID" = :sid AND R."授業ID" IN (
            SELECT DISTINCT T."授業ID" FROM "時間割" T WHERE T."学期" = :kiki
        ) 
    """)
    records = db.session.execute(sql_records, {"sid": student_id, "kiki": selected_kiki}).fetchall()

    for status, subject_name in records:
        if subject_name in grouped_attendance:
            if status == "出席": grouped_attendance[subject_name]["attendance_count"] += 1
            elif status == "遅刻": grouped_attendance[subject_name]["tardy_count"] += 1
            elif status == "欠席": grouped_attendance[subject_name]["absent_count"] += 1

    # 4. 出席率を計算
    report_data_summary = []
    for subject, data in grouped_attendance.items():
        total_classes_so_far = data["total_classes_so_far"] 
        if total_classes_so_far > 0:
            data["attendance_rate"] = round((data["attendance_count"] / total_classes_so_far) * 100, 1)
        else:
            data["attendance_rate"] = 0.0

        row = {
            "subject": subject,
            "attendance_rate": data["attendance_rate"],
            "total_classes": data["total_classes_planned"], 
            "total_classes_so_far": data["total_classes_so_far"], 
            "attendance_count": data["attendance_count"],
            "tardy_count": data["tardy_count"],
            "absent_count": data["absent_count"],
        }
        report_data_summary.append(row)
        
    return render_template("my_attendance.html", 
                           student_id=student_id, student_name=student_name,
                           report_data=report_data_summary, selected_kiki=selected_kiki, 
                           kikis=["1", "2", "3", "4"])

@app.route("/my_attendance_detail", methods=["GET"])
@login_required 
def my_attendance_detail():
    """(レポート) 個人別出席詳細 (text()版)"""
    student_id = request.args.get("student_id")
    selected_kiki = request.args.get("kiki", "1")
    subject_name_filter = request.args.get("subject")

    if not student_id or not student_id.isdigit():
        return redirect("/my_attendance")
    student_id = int(student_id)
    
    student_info = 学生.query.get(student_id)
    student_name = student_info.学生名 if student_info else "不明な学生"

    # PostgreSQL/SQLite両方でROWIDの代わりにPK(ID)を使う
    sql_records = text("""
        SELECT R."ID", R."出席時刻", R."状態", S."授業科目名"
        FROM "出席記録" R
        JOIN "授業" S ON R."授業ID" = S."授業ID"
        WHERE R."学生ID" = :sid AND R."授業ID" IN (
            SELECT DISTINCT T."授業ID" FROM "時間割" T WHERE T."学期" = :kiki
        )
        ORDER BY S."授業科目名", R."出席時刻"
    """)
    records = db.session.execute(sql_records, {"sid": student_id, "kiki": selected_kiki}).fetchall()
    
    grouped_attendance = OrderedDict()
    status_map = {"出席": "○", "遅刻": "△", "欠席": "×"}
    max_recorded_count = 0

    for record_id, timestamp, status, subject_name in records:
        if subject_name not in grouped_attendance:
            grouped_attendance[subject_name] = {"records": []}
        
        # timestamp は datetime オブジェクトのはず
        date_part = timestamp.strftime('%Y-%m-%d')
        display_status = status_map.get(status, status) 
        grouped_attendance[subject_name]["records"].append(
            (record_id, display_status, date_part, status) 
        )
        max_recorded_count = max(max_recorded_count, len(grouped_attendance[subject_name]["records"]))
            
    report_data_detail = []
    
    if subject_name_filter and subject_name_filter not in grouped_attendance:
        report_data_detail.append({"subject": subject_name_filter})
        max_recorded_count = 0 
    
    for subject, data in grouped_attendance.items():
        if subject_name_filter and subject != subject_name_filter:
            continue
        row = {"subject": subject}
        
        for i in range(max_recorded_count):
            count_str = str(i + 1) 
            if i < len(data["records"]):
                record_id, status_symbol, date, original_status = data["records"][i]
                try:
                    formatted_date = datetime.strptime(date, '%Y-%m-%d').strftime('%m.%d')
                except ValueError:
                    formatted_date = date
                
                row[f"count_{count_str}_id"] = record_id
                row[f"count_{count_str}_status"] = status_symbol 
                row[f"count_{count_str}_display"] = f"{status_symbol} ({formatted_date})" 
                row[f"count_{count_str}_original_status"] = original_status
            else:
                row[f"count_{count_str}_id"] = None
                row[f"count_{count_str}_status"] = '-'
                row[f"count_{count_str}_display"] = '-'
                row[f"count_{count_str}_original_status"] = None
            
        report_data_detail.append(row)
        
    return render_template("my_attendance_detail.html", 
                           student_id=student_id, student_name=student_name,
                           report_data=report_data_detail, max_count=max_recorded_count,
                           selected_kiki=selected_kiki, kikis=["1", "2", "3", "4"],
                           subject_filter=subject_name_filter)

@app.route("/report_summary", methods=["GET"])
@login_required
def report_summary():
    """(レポート) 授業別出席管理表 (text()版)"""
    
    sql_subjects = text("""
        SELECT DISTINCT S."授業ID", S."授業科目名", T."学期" 
        FROM "授業" S 
        JOIN "時間割" T ON S."授業ID" = T."授業ID" 
        WHERE S."授業ID" != 0 
        ORDER BY T."学期", S."授業ID"
    """)
    all_subjects = db.session.execute(sql_subjects).fetchall()
    
    report_data = None
    selected_subject_key = request.args.get("subject_key") 
    selected_subject_id = None
    selected_kiki = None
    current_kiki_str = get_current_kiki()

    if selected_subject_key:
        try:
            parts = selected_subject_key.split('-')
            if len(parts) == 2:
                selected_kiki = parts[0]
                selected_subject_id = int(parts[1])
            else:
                raise ValueError("無効なsubject_keyです")

            sql_schedule = text('SELECT T."曜日", T."学期", COUNT(T."時限") AS periods_per_day FROM "時間割" T WHERE T."授業ID" = :sid AND T."学期" = :kiki GROUP BY T."学期", T."曜日"')
            schedule_entries = db.session.execute(sql_schedule, {"sid": selected_subject_id, "kiki": selected_kiki}).fetchall()
            
            if schedule_entries:
                total_classes_planned = 0
                total_classes_so_far = 0 

                for target_yobi_str, target_kiki_str, periods_per_day in schedule_entries:
                    target_yobi_code = YOBI_MAP.get(target_yobi_str)
                    target_kiki_num = int(selected_kiki) 

                    if target_yobi_code is not None:
                        sql_days_planned = text('SELECT COUNT("日付") FROM "授業計画" WHERE "期" = :kiki AND "授業曜日" = :code')
                        total_days_planned = db.session.execute(sql_days_planned, {"kiki": target_kiki_num, "code": target_yobi_code}).scalar()
                        total_classes_planned += total_days_planned * periods_per_day
                        
                        sql_days_so_far = text("""
                            SELECT COUNT("日付") FROM "授業計画" 
                            WHERE "期" = :kiki AND "授業曜日" = :code 
                            AND TO_DATE(REPLACE("日付", '/', '-'), 'YYYY-MM-DD') <= CURRENT_DATE
                        """)
                        total_days_so_far = db.session.execute(sql_days_so_far, {"kiki": target_kiki_num, "code": target_yobi_code}).scalar()
                        total_classes_so_far += total_days_so_far * periods_per_day
                
                max_count = total_classes_planned 

                students = 学生.query.order_by(学生.学生ID).all()
                
                summary = []
                for student in students:
                    sql_counts = text("""
                        SELECT R."状態", COUNT(R."状態") 
                        FROM "出席記録" R 
                        JOIN "時間割" T ON R."授業ID" = T."授業ID" AND R."時限" = T."時限" 
                        WHERE R."授業ID" = :sid AND R."学生ID" = :stid AND T."学期" = :kiki 
                        GROUP BY R."状態"
                    """)
                    counts = dict(db.session.execute(sql_counts, {"sid": selected_subject_id, "stid": student.学生ID, "kiki": selected_kiki}).fetchall())
                    
                    attended_count = counts.get('出席', 0)
                    tardy_count = counts.get('遅刻', 0)
                    absent_count = counts.get('欠席', 0)
                    
                    attendance_rate = 0.0
                    if total_classes_so_far > 0:
                        attendance_rate = round((attended_count / total_classes_so_far) * 100, 1)
                    
                    total_recorded_so_far = attended_count + tardy_count + absent_count
                    unrecorded_count = total_classes_so_far - total_recorded_so_far
                    if unrecorded_count < 0: unrecorded_count = 0
                    
                    summary.append({
                        'id': student.学生ID, 'name': student.学生名,
                        'max_count': max_count, 'attendance_rate': attendance_rate,
                        'total_classes_so_far': total_classes_so_far,
                        'counts': {
                            '出席': attended_count, '遅刻': tardy_count,
                            '欠席': absent_count, 'その他': unrecorded_count
                        }
                    })
                report_data = summary
            
        except ValueError:
             selected_subject_key = None
            
    return render_template("report_summary.html", 
                           all_subjects=all_subjects,
                           report_data=report_data,
                           selected_subject_key=selected_subject_key,
                           current_kiki_str=current_kiki_str) 

@app.route("/export/report_summary")
@login_required
def export_report_summary():
    """(エクスポート) 授業別レポートをCSVでダウンロード (text()版)"""
    selected_subject_key = request.args.get("subject_key")
    if not selected_subject_key:
        return redirect(url_for('report_summary'))

    try:
        parts = selected_subject_key.split('-')
        if len(parts) == 2:
            selected_kiki = parts[0]
            selected_subject_id = int(parts[1])
        else:
            raise ValueError("無効なsubject_keyです")

        subject_info = 授業.query.get(selected_subject_id)
        subject_name = subject_info.授業科目名 if subject_info else "UnknownSubject"
        
        # (report_summary と同じロジック)
        sql_schedule = text('SELECT T."曜日", T."学期", COUNT(T."時限") AS periods_per_day FROM "時間割" T WHERE T."授業ID" = :sid AND T."学期" = :kiki GROUP BY T."学期", T."曜日"')
        schedule_entries = db.session.execute(sql_schedule, {"sid": selected_subject_id, "kiki": selected_kiki}).fetchall()
        
        total_classes_planned = 0
        total_classes_so_far = 0
        
        if schedule_entries:
            for target_yobi_str, target_kiki_str, periods_per_day in schedule_entries:
                target_yobi_code = YOBI_MAP.get(target_yobi_str)
                target_kiki_num = int(selected_kiki)

                if target_yobi_code is not None:
                    sql_days_planned = text('SELECT COUNT("日付") FROM "授業計画" WHERE "期" = :kiki AND "授業曜日" = :code')
                    total_days_planned = db.session.execute(sql_days_planned, {"kiki": target_kiki_num, "code": target_yobi_code}).scalar()
                    total_classes_planned += total_days_planned * periods_per_day
                    
                    sql_days_so_far = text("""
                        SELECT COUNT("日付") FROM "授業計画" 
                        WHERE "期" = :kiki AND "授業曜日" = :code 
                        AND TO_DATE(REPLACE("日付", '/', '-'), 'YYYY-MM-DD') <= CURRENT_DATE
                    """)
                    total_days_so_far = db.session.execute(sql_days_so_far, {"kiki": target_kiki_num, "code": target_yobi_code}).scalar()
                    total_classes_so_far += total_days_so_far * periods_per_day

        students = 学生.query.order_by(学生.学生ID).all()
        
        report_data = [] 
        for student in students:
            sql_counts = text('SELECT R."状態", COUNT(R."状態") FROM "出席記録" R JOIN "時間割" T ON R."授業ID" = T."授業ID" AND R."時限" = T."時限" WHERE R."授業ID" = :sid AND R."学生ID" = :stid AND T."学期" = :kiki GROUP BY R."状態"')
            counts = dict(db.session.execute(sql_counts, {"sid": selected_subject_id, "stid": student.学生ID, "kiki": selected_kiki}).fetchall())
            
            attended_count = counts.get('出席', 0)
            tardy_count = counts.get('遅刻', 0)
            absent_count = counts.get('欠席', 0)
            
            attendance_rate = 0.0
            if total_classes_so_far > 0:
                attendance_rate = round((attended_count / total_classes_so_far) * 100, 1)

            total_recorded_so_far = attended_count + tardy_count + absent_count
            unrecorded_count = total_classes_so_far - total_recorded_so_far
            if unrecorded_count < 0: unrecorded_count = 0
            
            report_data.append({
                'id': student.学生ID, 'name': student.学生名,
                'max_count': total_classes_planned, 'total_classes_so_far': total_classes_so_far, 
                'attendance_rate': attendance_rate, 'attended_count': attended_count,
                'tardy_count': tardy_count, 'absent_count': absent_count,
                'unrecorded_count': unrecorded_count
            })

        si = io.StringIO()
        si.write('\ufeff') 
        headers = ['学生ID', '学生名', '出席率 (%)', '総授業回数(予定)', '今日までの授業回数', '出席', '遅刻', '欠席', '未記録(今日まで)']
        writer = csv.writer(si)
        writer.writerow(headers) 

        for row in report_data:
            writer.writerow([
                f"=\"{row['id']}\"", 
                row['name'], row['attendance_rate'],
                row['max_count'], row['total_classes_so_far'], 
                row['attended_count'], row['tardy_count'],
                row['absent_count'], row['unrecorded_count']
            ])

        output = make_response(si.getvalue())
        today = datetime.now().strftime('%Y%m%d')
        filename_str = f"{today}_{selected_kiki}期_{subject_name}_出席レポート.csv"
        filename_encoded = quote(filename_str) 

        output.headers["Content-Disposition"] = (
            f"attachment; "
            f"filename=\"report.csv\"; "
            f"filename*=UTF-8''{filename_encoded}"
        )
        output.headers["Content-type"] = "text/csv; charset=utf-8"
        return output

    except Exception as e:
        db.session.rollback()
        print(f"Error exporting CSV: {e}")
        return redirect(url_for('report_summary'))

@app.route("/send_schedule_email")
@login_required
def send_schedule_email_route():
    """(メール送信) テストメールを送信"""
    try:
        msg = Message(
            subject="時間割情報のお知らせ（テスト）", 
            sender=app.config['MAIL_USERNAME'], 
            recipients=[app.config['MAIL_USERNAME']] # 宛先 (自分自身)
        )
        msg.body = "これは出席管理システムからのテストメールです。"
        
        mail.send(msg) 
        flash("✅ テストメールが正常に送信されました。", "success")
        
    except Exception as e:
        flash(f"❌ メールの送信中にエラーが発生しました: {e}", "error")
        
    return redirect(url_for('index'))

# --- 11. LINE Bot Webhook (SQLAlchemy版) ---

if handler:
    @app.route("/callback", methods=['POST'])
    def callback():
        """LINEからのWebhookを受け取る"""
        signature = request.headers['X-Line-Signature']
        body = request.get_data(as_text=True)
        app.logger.info("Request body: " + body)
        try:
            handler.handle(body, signature)
        except InvalidSignatureError:
            print("Invalid signature. Please check your channel secret.")
            abort(400) # ⬅️ abort を import する必要があります
        return 'OK'

    @handler.add(MessageEvent, message=TextMessage)
    def handle_message(event):
        """LINEのテキストメッセージを処理する"""
        received_text = event.message.text
        user_id = event.source.user_id
        reply_message = "" 

        if received_text == "今日の時間割" or received_text == "明日の時間割":
        # 曜日判定（今日は0、明日は1）
        days_ahead = 0 if received_text == "今日の時間割" else 1
        target_date = now + timedelta(days=days_ahead) # timedeltaをインポートする必要があります
        
        reply_message = get_schedule_for_line(target_date) # ⚠️ 新しいヘルパー関数を定義します
        
        elif received_text == "出席サマリー":
            # 出席サマリー機能（ここではシンプルに実装）
            reply_message = get_attendance_summary_for_line(user_id) # ⚠️ 新しいヘルパー関数を定義します
            
        elif received_text == "退室":
            # 在室履歴を終了させるロジック
            reply_message = process_exit_record(user_id) # ⚠️ 新しいヘルパー関数を定義します
        
        elif received_text == "気温":
            if sensor_data:
                 latest = sensor_data[-1]
                 reply_message = f"現在の気温は {latest.get('temperature')}℃ です。"
            else:
                 reply_message = "センサーデータがまだありません。"
                 
        else:
            reply_message = f"「{received_text}」を受け取りました。"

        quick_reply_buttons = QuickReply(items=[
            QuickReplyButton(action=MessageAction(label="今日の時間割", text="今日の時間割")),
            QuickReplyButton(action=MessageAction(label="明日の時間割", text="明日の時間割")),
            QuickReplyButton(action=MessageAction(label="出席サマリー", text="出席サマリー")),
            QuickReplyButton(action=MessageAction(label="退室する", text="退室")), # 在室記録を終わらせる
        ])

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_message, quick_reply=quick_reply_buttons)
        )

# --- 12. 実行 ---

if __name__ == "__main__":
    # アプリケーションコンテキスト内でデータベースを作成
    with app.app_context():
        db.create_all() # 存在しないテーブルのみ作成
        
    app.run(host="0.0.0.0", port=5000, threaded=True, debug=True)