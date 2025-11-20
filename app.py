import os
import io
import csv
import requests
import base64
from dotenv import load_dotenv # ⬅️ これを追加
from flask import Flask, render_template, request, jsonify, redirect, url_for, make_response, flash, get_flashed_messages, abort
from datetime import datetime, timedelta, time
from collections import OrderedDict
from urllib.parse import quote
# ... (Flask, datetime, csv などのimport) ...
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

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

# ▼▼▼ 以下を追記 (または既存のimportに追加) ▼▼▼
from linebot.models import (
    FlexSendMessage, BubbleContainer, BoxComponent, 
    TextComponent, SeparatorComponent,
    ButtonComponent, URIAction
)

# --- ▼ SQLAlchemy (B案) に変更 ▼ ---
from flask_sqlalchemy import SQLAlchemy
# 以下の行が重要です。必要な型と関数だけをインポートします。
from sqlalchemy import Integer, String, ForeignKey, func, UniqueConstraint, text, Column, Computed 
from sqlalchemy import Time as SQLTime, DateTime as SQLDateTime
from sqlalchemy.orm import relationship
from sqlalchemy.exc import IntegrityError
from werkzeug.utils import secure_filename # ファイル名を安全にする機能
app = Flask(__name__)

UPLOAD_DIR = 'uploaded_images'
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR) # フォルダがなければ自動で作る

auth_commands = {}
# --- ▲ SQLAlchemy に変更 ▲ ---
TEMP_EXIT_STATUS = "一時退出中"
# デフォルト値を削除し、設定がない場合はNoneにする（またはエラーにする）
SCHEDULE_API_TOKEN = os.environ.get('SCHEDULE_API_TOKEN')
app.secret_key = os.environ.get('SECRET_KEY')

# 必須チェックを入れるとさらに安全
if not app.secret_key:
    raise ValueError("No SECRET_KEY set for Flask application")
load_dotenv()
BASE_URL = os.environ.get('BASE_URL', 'http://127.0.0.1:5000') # 開発中はローカルを指す

# ----------------------------------------------------------------------
# 1. データベース設定 (PostgreSQL/SQLite両対応)
# ----------------------------------------------------------------------

DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///zaiseki.db')
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ECHO'] = False # Trueにすると実行SQLをコンソールに出力

db = SQLAlchemy(app)

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


# ----------------------------------------------------------------------
# 2. Flask-Login と Mail の設定 
# ----------------------------------------------------------------------

app.secret_key = os.environ.get('SECRET_KEY', 'default_fallback_key_if_not_set')

# .env からメール設定を読み込む
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587  # 465 から 587 に変更
app.config['MAIL_USE_TLS'] = True  # TLSを有効化
app.config['MAIL_USE_SSL'] = False # SSLを無効化
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')
mail = Mail(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login' 
login_manager.login_message = "このページにアクセスするにはログインが必要です。"
login_manager.login_message_category = "error"

# ----------------------------------------------------------------------
# 3. ユーザーモデル (Flask-Login用)
# ----------------------------------------------------------------------

class User(UserMixin):
    def __init__(self, id, username, password):
        self.id = id
        self.username = username
        self.password = password # 🚨 注意: 管理者パスワードはハッシュ化されていません
        
    # 🚨 get_idメソッドを追加
    def get_id(self):
        # ユーザーIDとして「admin-」というプレフィックスを付けて返す
        return f"admin-{self.id}"

admin_user_db = {
    "1": User("1", "admin", os.environ.get('ADMIN_PASSWORD'))
}

# app.py 内の関数


@login_manager.user_loader
def load_user(user_id):
    # user_id は 'admin-1' または 'student-222521301' の形式で来る
    
    if user_id.startswith('admin-'):
        # 管理者ユーザーを読み込む
        admin_id = user_id.split('-')[1]
        return admin_user_db.get(admin_id)
        
    elif user_id.startswith('student-'):
        # 学生ユーザーを読み込む
        try:
            student_id = int(user_id.split('-')[1])
            return 学生.query.get(student_id)
        except:
            return None
    return None

# ----------------------------------------------------------------------
# 4. データベースモデル (ORM クラス) の定義
# ----------------------------------------------------------------------

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
    def __repr__(self):
        return f"<LineUser line_user_id='{self.line_user_id}' student_id={self.student_id}>"
    
class FaceData(db.Model):
    __tablename__ = 'face_data'
    student_id = db.Column(db.Integer, db.ForeignKey('学生.学生ID', ondelete='CASCADE'), primary_key=True)
    face_encoding = db.Column(db.Text, nullable=False) 
    student = relationship("学生", back_populates="face_data")    

class 学生(UserMixin, db.Model):
    __tablename__ = '学生'
    学生ID = db.Column(db.Integer, primary_key=True)
    学生名 = db.Column(db.String, nullable=False, unique=True)
    
    # 🚨 新規追加: 学生用のパスワードハッシュを保存するカラム
    password_hash = db.Column(db.String(256), nullable=True) 
    parent_email = db.Column(db.String(120), nullable=True)
    # (既存のリレーションシップ定義はそのまま)
    出席記録s = db.relationship('出席記録', back_populates='学生', cascade="all, delete-orphan")
    在室履歴s = db.relationship('在室履歴', back_populates='学生', cascade="all, delete-orphan")
    line_user = relationship("LineUser", back_populates="student", uselist=False, cascade="all, delete-orphan")
    face_data = relationship("FaceData", back_populates="student", uselist=False, cascade="all, delete-orphan")

    # 🚨 Flask-Loginのためのメソッドを追加
    def get_id(self):
        # ユーザーIDとして「student-」というプレフィックスを付けて返す
        return f"student-{self.学生ID}"

    def set_password(self, password):
        # パスワードをハッシュ化して保存
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        # ハッシュ化されたパスワードと一致するかチェック
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
    report_type = db.Column(db.String(10), nullable=False) # '遅刻' or '欠席'
    reason = db.Column(db.String(500), nullable=True) # 連絡理由
    report_date = db.Column(SQLDateTime, nullable=False, default=datetime.now)
    is_resolved = db.Column(db.Boolean, default=False) # 管理者確認済みフラグ
    
    student = relationship("学生") # 学生情報を参照

def save_image(base64_data, student_id):
    try:
        # データURLスキームを取り除く (例: "data:image/jpeg;base64,..." の部分)
        if "base64," in base64_data:
            header, encoded = base64_data.split(",", 1)
        else:
            encoded = base64_data
            
        # 空白文字や改行が入っている場合があるので削除
        encoded = encoded.strip()
            
        # Base64デコード
        data = base64.b64decode(encoded)
        
        # ファイル名: YYYYMMDD_HHMMSS_学生ID.jpg
        filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{student_id}.jpg"
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        
        with open(filepath, "wb") as f:
            f.write(data)
        return filename
    except Exception as e:
        print(f"画像保存エラー: {e}")
        return None

def check_and_send_alert(student_id, subject_id):
    print(f"🔍 [DEBUG] アラート判定開始: 学生ID={student_id}, 授業ID={subject_id}")

    try:
        student = 学生.query.get(student_id)
        subject = 授業.query.get(subject_id)
        
        if not student or not subject:
            return

        # --- 出席率計算ロジック (ここは元のままでOK) ---
        current_kiki = get_current_kiki()
        kiki_int = int(current_kiki)
        
        sql_days = text('SELECT "曜日", COUNT("時限") FROM "時間割" WHERE "授業ID"=:sid AND "学期"=:kiki GROUP BY "曜日"')
        schedule_data = db.session.execute(sql_days, {"sid": subject_id, "kiki": current_kiki}).fetchall()
        
        total_so_far = 0
        for day_name, count in schedule_data:
            day_code = YOBI_MAP.get(day_name)
            if day_code is not None:
                sql_plan = text('SELECT COUNT(*) FROM "授業計画" WHERE "期"=:kiki AND "授業曜日"=:code AND TO_DATE(REPLACE("日付", \'/\', \'-\'), \'YYYY-MM-DD\') <= CURRENT_DATE')
                days_count = db.session.execute(sql_plan, {"kiki": kiki_int, "code": day_code}).scalar()
                total_so_far += (days_count * count)

        if total_so_far == 0: return

        sql_attend = text('SELECT COUNT(*) FROM "出席記録" WHERE "学生ID"=:sid AND "授業ID"=:subid AND "状態" IN (\'出席\', \'遅刻\', \'公欠\')')
        attended_count = db.session.execute(sql_attend, {"sid": student_id, "subid": subject_id}).scalar()

        rate = round((attended_count / total_so_far) * 100, 1)
        # ----------------------------------------------

        # ★ここから下を変更（GASへ送信依頼）★
        if rate < 80:
            print(f"[DEBUG] 出席率 {rate}% (80%未満) なので通知を送ります")
            
            msg_subject = f"【出席率注意】{student.学生名}さん - {subject.授業科目名}"
            msg_body = (
                f"出席管理システムからの自動通知\n"
                f"--------------------------------\n"
                f"学生: {student.学生名}\n"
                f"授業: {subject.授業科目名}\n"
                f"出席率: {rate}% ({attended_count}/{total_so_far})\n"
                f"--------------------------------"
            )
            
            # 宛先リスト作成
            recipients = [os.environ.get('MAIL_USERNAME')] # 管理者
            if student.parent_email:
                recipients.append(student.parent_email)
            
            # GASにデータを投げる
            gas_url = os.environ.get('GAS_API_URL')
            gas_token = os.environ.get('GAS_AUTH_TOKEN')
            
            if gas_url and gas_token:
                payload = {
                    "to": ",".join(recipients), # カンマ区切りで送る
                    "subject": msg_subject,
                    "body": msg_body,
                    "auth_token": gas_token
                }
                requests.post(gas_url, json=payload)
                print("✅ [SUCCESS] GASにメール送信を依頼しました")
            else:
                print("⚠️ [ERROR] GAS_API_URL または GAS_AUTH_TOKEN が設定されていません")

    except Exception as e:
        print(f" [ERROR] アラート処理エラー: {e}")

@app.route("/api/portal_face_auth", methods=["POST"])
@login_required  # 🚨 ログイン必須にする
def api_portal_face_auth():
    """ポータルからの顔認証出席登録"""
    try:
        # 1. 画像データの受け取り
        data = request.get_json()
        image_data = data.get("image")
        
        if not image_data:
            return jsonify({"status": "error", "message": "画像データがありません"}), 400

        # 2. ログイン中のユーザー情報を取得
        student = current_user # Flask-Loginが自動で特定してくれる
        student_id = student.学生ID

        # --- ここに「顔認証ロジック」が入ります ---
        # (今回は簡易的に「画像を保存して出席」としますが、
        #  本来はここで saved_filename の画像と student_id の登録顔モデルを比較します)
        
        # 画像を保存（証拠として）
        saved_filename = save_image(image_data, student_id)
        print(f"📸 [Web認証] {student.学生名} (ID:{student_id}) の画像を保存: {saved_filename}")

        # 3. 授業判定（前後20分のバッファを持たせる）
        now = datetime.now()
        target_period = None
        
        all_periods = TimeTable.query.all()
        for p in all_periods:
            p_start = datetime.combine(now.date(), p.開始時刻)
            p_end = datetime.combine(now.date(), p.終了時刻)
            
            # 前後20分余裕を持たせる
            if (p_start - timedelta(minutes=20)) <= now <= (p_end + timedelta(minutes=20)):
                target_period = p.時限
                break
        
        if not target_period:
            return jsonify({"status": "error", "message": "現在は授業時間外です"}), 200

        # 4. 重複チェック & 登録
        existing = 出席記録.query.filter_by(
            学生ID=student_id, 
            授業ID=0, # まだ授業IDが特定できていない場合（本来は時間割から取得）
            出席日付=now.date(), 
            時限=target_period
        ).first()
        
        # ※正確な授業IDを取得するロジック
        # 今日の曜日・時限から授業を特定
        today_yobi_str = YOBI_MAP_REVERSE.get((now.weekday() + 1) % 7)
        kiki = get_current_kiki()
        class_row = 時間割.query.filter_by(学期=kiki, 曜日=today_yobi_str, 時限=target_period).first()
        subject_id = class_row.授業ID if class_row else 0
        
        if subject_id == 0:
             return jsonify({"status": "error", "message": "この時間は授業がありません"}), 200

        # 再チェック（授業ID込み）
        existing = 出席記録.query.filter_by(学生ID=student_id, 授業ID=subject_id, 出席日付=now.date(), 時限=target_period).first()

        if not existing:
            new_attendance = 出席記録(
                学生ID=student_id,
                授業ID=subject_id,
                出席時刻=now,
                状態="出席", # 顔認証OKなら出席
                時限=target_period
            )
            db.session.add(new_attendance)
            db.session.commit()
            return jsonify({"status": "success", "message": f"{target_period}限 ({class_row.授業.授業科目名}) の出席を受け付けました！"})
        else:
            return jsonify({"status": "info", "message": "既に出席済みです"})

    except Exception as e:
        print(f"Portal Auth Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# ----------------------------------------------------------------------
# 5. データベースに挿入
# ----------------------------------------------------------------------

@app.cli.command('init-db')
def init_db_command():
    
    # --- データ投入ロジック ---
    from datetime import time 
    from sqlalchemy.exc import IntegrityError
    
    with app.app_context():
        
        print("【INFO】既存のスキーマ(public)を CASCADE で強制的に削除します...")
        try:
            # ⬅️ 以前の 'db.drop_all()' の代わりに、より強力なRAW SQLを実行
            # これで 'face_auth' のような幽霊テーブルも強制削除される
            db.session.execute(text('DROP SCHEMA public CASCADE;'))
            db.session.execute(text('CREATE SCHEMA public;'))
            db.session.commit()
            print("【INFO】スキーマの再作成が完了しました。")
        except Exception as e:
            # (初回デプロイ時など、スキーマが存在しない場合はエラーになるが問題ない)
            print(f"【WARNING】スキーマの削除に失敗しました (初回デプロイの場合は無視してOK): {e}")
            db.session.rollback() # 失敗した場合はロールバック

        # 2. すべてのテーブルを（まっさらなスキーマに）作成
        # これで password_hash や face_data を持つ新しいテーブルが正しく作られる
        db.create_all()
        print("データベース初期化完了。")
        
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

# ----------------------------------------------------------------------
# 6. ヘルパー関数 (SQLAlchemy版)
# ----------------------------------------------------------------------

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
    """指定された日付の時間割を「BubbleContainer」または「エラー文字列」で返す"""
    
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
    
    # 時間割データの取得 (既存のロジック)
    schedule_rows = db.session.query(
        時間割, 授業.授業科目名, 授業.担当教員
    ).outerjoin(授業, 時間割.授業ID == 授業.授業ID)\
     .filter(時間割.学期 == kiki, 時間割.曜日 == yobi_to_use)\
     .order_by(時間割.時限).all()
     
    if not schedule_rows:
        # エラー時や休校日は「文字列」を返す
        return f"📅 {date_str} ({yobi_str}):\n授業計画が見つからないか、休校日です。"

    # --- ▼▼▼ ここからFlex Messageの組み立て ▼▼▼ ---
    
    body_contents = []
    
    # 1. ヘッダー部分
    body_contents.append(TextComponent(
        text=f"📅 {date_str} ({yobi_to_use})",
        weight="bold", size="lg", margin="md"
    ))
    body_contents.append(TextComponent(
        text=f"第{kiki}期 の時間割",
        size="sm", color="#666666", margin="sm"
    ))
    body_contents.append(SeparatorComponent(margin="lg"))

    # 2. 各時間割
    for row in schedule_rows: #
        time_row = TimeTable.query.get(row[0].時限)
        time_str = f"({time_row.開始時刻.strftime('%H:%M')}-{time_row.終了時刻.strftime('%H:%M')})" if time_row else ""
        
        subject_name = row[1] if row[1] else (row[0].備考 if row[0].備考 else "空き時間")
        
        # ▼▼▼ 修正ポイント ▼▼▼
        # teacher 変数には None が入るようにする
        teacher = row[2] if row[2] else None 
        
        # 授業ごとのBoxコンポーネントの中身を動的に作成
        period_contents = [
            TextComponent(
                text=f"{row[0].時限}限 {time_str}",
                weight="bold",
                color="#1E90FF" # (時限の色)
            ),
            TextComponent(
                text=f"{subject_name}",
                size="md",
                weight="bold",
                wrap=True
            )
        ]
        
        # ▼▼▼ 修正ポイント ▼▼▼
        # teacher 変数が None でない（中身がある）場合のみ、
        # 教員名の TextComponent をリストに追加する
        if teacher:
            period_contents.append(
                TextComponent(
                    text=f"{teacher}",
                    size="sm",
                    color="#666666",
                    wrap=True
                )
            )
        # ▲▲▲ 修正ここまで ▲▲▲

        period_box = BoxComponent(
            layout="vertical",
            margin="lg",
            spacing="sm",
            contents=period_contents # ⇐ 動的に作成したリストを使用
        )
        
        body_contents.append(period_box)
        body_contents.append(SeparatorComponent(margin="lg")) # 授業ごとの区切り線

    # 3. Bubbleコンテナとしてまとめる
    bubble = BubbleContainer(
        body=BoxComponent(
            layout="vertical",
            contents=body_contents
        )
    )
    
    # BubbleContainerオブジェクトを返す
    return bubble
    
def get_attendance_summary_for_line(line_user_id):
    """(授業ごと版) LINEユーザーIDに対応する学生の出席サマリーを「BubbleContainer」または「エラー文字列」で返す"""
    
    student_id = get_student_id_from_line_user(line_user_id) #
    if student_id is None:
        return "⚠️ あなたの学生IDが登録されていません。\n「登録:学生ID」の形式で一度登録してください。"
    
    student = 学生.query.get(student_id) #
    selected_kiki = get_current_kiki() #
    kiki_int = int(selected_kiki)

    # --- ▼▼▼ /my_attendance (Web版) のロジックを流用 ▼▼▼ ---
    
    # 1. 履修科目の一覧を取得
    sql_enrolled = text("""
        SELECT DISTINCT S."授業科目名", S."授業ID"
        FROM "時間割" T
        JOIN "授業" S ON T."授業ID" = S."授業ID"
        WHERE T."学期" = :kiki AND T."授業ID" != 0 
        ORDER BY S."授業科目名"
    """) #
    enrolled_subjects = db.session.execute(sql_enrolled, {"kiki": selected_kiki}).fetchall()

    if not enrolled_subjects:
        return f"📅 第{selected_kiki}期: \n履修中の授業データが見つかりませんでした。"

    # 2. 各授業の出席データを集計
    report_data = []
    
    for subject_name, subject_id in enrolled_subjects:
        
        # 2a. その授業の「今日までの総コマ数」を計算
        sql_schedule = text('SELECT T."曜日", COUNT(T."時限") FROM "時間割" T WHERE T."授業ID" = :sid AND T."学期" = :kiki GROUP BY T."曜日"')
        schedule_data = db.session.execute(sql_schedule, {"sid": subject_id, "kiki": selected_kiki}).fetchall()
        
        total_classes_so_far = 0
        for day_of_week, periods_per_day in schedule_data:
            day_code = YOBI_MAP.get(day_of_week) #
            if day_code is not None:
                sql_days_so_far = text("""
                    SELECT COUNT("日付") FROM "授業計画" 
                    WHERE "期" = :kiki AND "授業曜日" = :code 
                    AND TO_DATE(REPLACE("日付", '/', '-'), 'YYYY-MM-DD') <= CURRENT_DATE
                """) #
                total_days_so_far = db.session.execute(sql_days_so_far, {"kiki": kiki_int, "code": day_code}).scalar()
                total_classes_so_far += total_days_so_far * periods_per_day
        
        # 2b. その授業の「出席記録」を集計
        sql_records = text("""
            SELECT R."状態", COUNT(R."状態")
            FROM "出席記録" R
            JOIN "授業計画" P ON R."出席日付" = TO_DATE(REPLACE(P."日付", '/', '-'), 'YYYY-MM-DD')
            WHERE R."学生ID" = :sid 
              AND P."期" = :kiki_int
              AND R."授業ID" = :subject_id
            GROUP BY R."状態"
        """)
        records_count = dict(db.session.execute(sql_records, {
            "sid": student_id, 
            "kiki_int": kiki_int,
            "subject_id": subject_id
        }).fetchall())

        attendance_count = records_count.get('出席', 0)
        tardy_count = records_count.get('遅刻', 0)
        absent_count_db = records_count.get('欠席', 0) # ⬅️ DB上の欠席

        # 2c. 出席率を計算
        attendance_rate = 0.0
        if total_classes_so_far > 0:
            attendance_rate = round((attendance_count / total_classes_so_far) * 100, 1)
        
        # 2d. 「未記録」を計算して「欠席」に合算
        total_recorded = attendance_count + tardy_count + absent_count_db
        unrecorded_count = total_classes_so_far - total_recorded
        if unrecorded_count < 0: unrecorded_count = 0
        
        total_absent = absent_count_db + unrecorded_count # ⬅️ 合算

        # 2e. データを一時保存
        report_data.append({
            "subject_name": subject_name,
            "rate": attendance_rate,
            "total_so_far": total_classes_so_far,
            "attendance": attendance_count,
            "tardy": tardy_count,
            "absent": total_absent # ⬅️ 合算した値を渡す
        })

    # --- ▲▲▲ 計算ロジックここまで ▲▲▲ ---

    # --- ▼▼▼ Flex Messageの組み立て ▼▼▼ ---
    
    body_contents = []
    
    # 1. ヘッダー
    body_contents.append(TextComponent(
        text=f"{student.学生名} さん",
        weight="bold", size="lg", margin="md"
    ))
    body_contents.append(TextComponent(
        text=f"第{selected_kiki}期 出席サマリー (授業ごと)",
        size="sm", color="#666666", margin="sm", wrap=True
    ))

    # 2. 各授業の内訳
    for item in report_data:
        body_contents.append(SeparatorComponent(margin="lg"))
        
        # (授業ごとのBoxコンポーネント)
        subject_box = BoxComponent(
            layout="vertical",
            margin="lg",
            spacing="sm",
            contents=[
                # 授業名
                TextComponent(
                    text=f"■ {item['subject_name']}",
                    weight="bold",
                    size="md",
                    wrap=True
                ),
                # 出席率
                TextComponent(
                    text=f"{item['rate']}%",
                    weight="bold",
                    size="lg",
                    color="#1E90FF",
                    margin="sm"
                ),
                # 詳細 (Xコマ / Yコマ中)
                TextComponent(
                    text=f"出席 {item['attendance']} / 総計 {item['total_so_far']}コマ",
                    size="sm",
                    color="#666666",
                    wrap=True
                ),
                # 遅刻・欠席
                TextComponent(
                    text=f"(遅刻 {item['tardy']}, 欠席 {item['absent']})",
                    size="sm",
                    color="#AAAAAA",
                    wrap=True,
                    margin="sm"
                )
            ]
        )
        body_contents.append(subject_box)

    portal_url = f"{BASE_URL}{url_for('student_login')}" 

    footer_box = BoxComponent(
        layout="vertical",
        spacing="sm",
        contents=[
            SeparatorComponent(), # 区切り線
            ButtonComponent(
                style="link", # 'link'スタイルがシャレオツ
                height="sm",
                action=URIAction(label="Webポータルで詳細を見る", uri=portal_url)
            )
        ]
    )
    
    # --- ▲▲▲ 追記ここまで ▲▲▲ ---


    # Bubbleコンテナとしてまとめる
    bubble = BubbleContainer(
        body=BoxComponent(
            layout="vertical",
            contents=body_contents
        ),
        footer=footer_box  # ⇐ 追記したfooterをここで渡す
    )
    
    return bubble

#最終退室
def process_exit_record(line_user_id):
    """学生の在室履歴を終了させる（最終退室）"""
    # 🚨 修正箇所: 紐付けテーブルから学生IDを取得
    student_id = get_student_id_from_line_user(line_user_id) 
    if student_id is None:
        return "⚠️ あなたの学生IDが登録されていません。\n「登録:学生ID」の形式で一度登録してください。"

    # --- 既存のロジック ---
    existing_session = 在室履歴.query.filter_by(学生ID=student_id, 退室時刻=None).first()
    
    if existing_session:
        existing_session.退室時刻 = datetime.now()
        db.session.commit()
        # データベースから学生名を取得（エラー処理のため）
        student = 学生.query.get(student_id) 
        return f"🚪 {student.学生名}さんの最終退室時刻を記録しました。またのご利用をお待ちしております！"
    else:
        return "⚠️ 現在、入室記録が見つかりませんでした。"

#一時退出
def process_temporary_exit(line_user_id):
    """学生の一時退出を記録する"""
    # 🚨 修正箇所: 紐付けテーブルから学生IDを取得
    student_id = get_student_id_from_line_user(line_user_id) 
    if student_id is None:
        return "⚠️ 学生IDが登録されていません。「登録:学生ID」で登録してください。"

    # ... (既存のロジックを維持) ...
    existing_session = 在室履歴.query.filter_by(学生ID=student_id, 退室時刻=None).first()
    
    if existing_session and existing_session.備考 == TEMP_EXIT_STATUS:
        return "⚠️ すでに一時退出中です。戻られたら「戻りました」をタップしてください。"
    
    if existing_session:
        # 既存の在室記録の備考欄に一時退出ステータスを記録
        existing_session.備考 = TEMP_EXIT_STATUS
        db.session.commit()
        return "🚶 一時退出を記録しました。戻られましたら「戻りました」をタップしてください。"
    else:
        return "⚠️ 入室記録が見つかりません。カメラでの入室認証が必要です。"

#退出状態から戻る
def process_return_from_exit(line_user_id):
    """一時退出状態からの復帰を記録する"""
    # 🚨 修正箇所: 紐付けテーブルから学生IDを取得
    student_id = get_student_id_from_line_user(line_user_id) 
    if student_id is None:
        return "⚠️ 学生IDが登録されていません。「登録:学生ID」で登録してください。"

    # ... (既存のロジックを維持) ...
    existing_session = 在室履歴.query.filter_by(
        学生ID=student_id, 退室時刻=None, 備考=TEMP_EXIT_STATUS
    ).first()
    
    if existing_session:
        # 備考欄をリセットし、復帰を記録
        existing_session.備考 = None
        db.session.commit()
        return "🎉 おかえりなさい！在室記録を再開します。"
    else:
        return "⚠️ 一時退出中の記録が見つかりません。"

def 判定(時限, 登録時刻):
    row = TimeTable.query.get(時限)
    if not row: return "未定義"
    
    # row.開始時刻 は datetime.time オブジェクト
    開始 = datetime.combine(登録時刻.date(), row.開始時刻)
    経過 = (登録時刻 - 開始).total_seconds() / 60
    
    if 経過 <= 0: return "出席"
    elif 経過 <= 20: return "遅刻"
    else: return "欠席"

def get_student_id_from_line_user(line_user_id):
    """LINE User IDから学生IDを取得する"""
    mapping = LineUser.query.filter_by(line_user_id=line_user_id).first()
    return mapping.student_id if mapping else None

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

# ----------------------------------------------------------------------
# 7.APIルート
# ----------------------------------------------------------------------

@app.route("/logout")
@login_required  # ログインしている人だけがログアウトできる
def logout():
    """ログアウト処理"""
    logout_user() # セッションからユーザー情報を削除
    flash("✅ ログアウトしました。", "info")
    return redirect(url_for('index'))

# --- 7. メインページ (ダッシュボード) ---
@app.route("/")
@login_required
def index():
    students = 学生.query.order_by(学生.学生ID).all()
    
    message = None
    category = None
    messages = get_flashed_messages(with_categories=True)
    if messages:
        category, message = messages[0]

    unresolved_alerts_count = 0
    if current_user.is_authenticated: # ログインしているユーザーのみ件数を取得
        try:
            unresolved_alerts_count = db.session.query(ReportRecord.record_id).filter(ReportRecord.is_resolved == False).count()
        except Exception as e:
            print(f"アラート件数のカウントに失敗: {e}")
            # エラーが発生してもページは表示させる
            unresolved_alerts_count = 0        
    links = [
        {"url": "/attendance?kiki=1", "name": "出席登録 / 全体記録"},
        {"url": "/schedule", "name": "時間割表示"},
        {"url": "/edit_schedule?kiki=1", "name": "時間割編集"},
        {"url": "/manage_students", "name": "学生管理"},
        {"url": "/manage_subjects", "name": "授業科目管理"},
        {"url": "/alerts", "name": "📢 遅刻・欠席連絡 掲示板"},
    ]
    
    return render_template("index.html", 
                           links=links, 
                           students=[(s.学生ID, s.学生名) for s in students], # テンプレートが (id, name) を期待
                           message=message,
                           category=category,
                           unresolved_alerts_count=unresolved_alerts_count)

@app.route('/api/schedule_update', methods=['POST'])
def api_schedule_update():
    """
    外部サービスからの時間割クイック更新API。認証トークンが必要。
    """
    # 1. トークン認証
    token = request.form.get('token')
    if token != SCHEDULE_API_TOKEN:
        return jsonify({'error': 'Unauthorized: Invalid API token'}), 401

    try:
        # 2. 必須パラメータの取得
        kiki = request.form.get('kiki')
        day = request.form.get('day')
        period = request.form.get('period')
        subject_id = request.form.get('subject_id')
        remark = request.form.get('remark') # 5限目の備考用 (任意)
        
        # 🚨 必須フィールドの確認と型変換を強化
        if not all([kiki, day, period, subject_id]):
            return jsonify({'success': False, 'error': 'Missing required form data (kiki, day, period, subject_id)'}), 400

        kiki_str = str(kiki)
        day_str = str(day)
        
        # 🚨 period と subject_id は整数に変換し、失敗したらエラーを返す
        try:
            period_int = int(period)
            subject_id_int = int(subject_id)
        except ValueError:
            return jsonify({'success': False, 'error': 'Period or Subject ID is not a valid integer'}), 400

        # 3. データベース更新ロジック
        
        # 既存の時間割データを検索
        existing_schedule = 時間割.query.filter_by(
            学期=kiki, 
            曜日=day, 
            時限=period
        ).first()
        
        kiki_int = int(kiki)
        period_int = int(period)

        if period_int == 5: # 5限目（備考欄）の特殊処理
            if existing_schedule:
                existing_schedule.科目ID = None
                existing_schedule.備考 = remark
                db.session.commit()
                message = f"第{kiki_int}期 {day}曜 5限の備考を更新しました。"
            elif remark: # 5限目がなく、備考が指定されている場合のみ新規作成
                new_schedule = 時間割(学期=kiki_int, 曜日=day, 時限=period_int, 授業ID=None, 備考=remark)
                db.session.add(new_schedule)
                db.session.commit()
                message = f"第{kiki_int}期 {day}曜 5限の備考を新規作成しました。"
            else:
                message = f"第{kiki_int}期 {day}曜 5限に更新はありませんでした。"
                
        else: # 通常授業 (1〜4限)
            subject_id_int = int(subject_id) if subject_id and subject_id.isdigit() else 0

            if subject_id_int == 0: # 削除（空欄にする）
                if existing_schedule:
                    db.session.delete(existing_schedule)
                    db.session.commit()
                    message = f"第{kiki_int}期 {day}曜 {period_int}限の授業を削除しました。"
                else:
                    message = f"第{kiki_int}期 {day}曜 {period_int}限はすでに空欄です。"
            else:
                if existing_schedule:
                    existing_schedule.科目ID = subject_id_int
                    existing_schedule.備考 = None
                    db.session.commit()
                    message = f"第{kiki_int}期 {day}曜 {period_int}限の授業を更新しました。（科目ID: {subject_id_int}）"
                else:
                    new_schedule = 時間割(学期=kiki_int, 曜日=day, 時限=period_int, 授業ID=subject_id_int, 備考=None)
                    db.session.add(new_schedule)
                    db.session.commit()
                    message = f"第{kiki_int}期 {day}曜 {period_int}限の授業を新規作成しました。（科目ID: {subject_id_int}）"

        return jsonify({'success': True, 'message': message}), 200

    except Exception as e:
        db.session.rollback()
        print(f"Schedule update failed: {e}")
        return jsonify({'success': False, 'error': f'An internal error occurred: {str(e)}'}), 500
    
@app.route("/api/register_attendance", methods=["POST"]) 
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
            
        check_and_send_alert(student_id, class_id)
        
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

@app.route('/api/alerts_count')
@login_required # ログインしているユーザーのみが件数を取得できるようにする
def api_alerts_count():
    """(API) 未確認の遅刻・欠席連絡の件数をJSONで返す"""
    try:
        # データベースから未解決(is_resolved == False)の件数をカウント
        count = db.session.query(ReportRecord.record_id).filter(ReportRecord.is_resolved == False).count()
        return jsonify({'count': count})
    except Exception as e:
        app.logger.error(f"アラート件数のカウントに失敗: {e}")
        # エラーが起きても、フロントエンドがクラッシュしないように 0 を返す
        return jsonify({'count': 0, 'error': str(e)}), 500

@app.route("/api/status")
def api_status():
    """(ダッシュボードAPI) リアルタイム在室状況を返す + 自動出席チェック"""
    
    # 1. まず「在室中」の学生リストを取得 (学生ID, 教室名, 入室時刻, 備考)
    active_sessions_data = db.session.query(
        在室履歴.学生ID, 教室.教室名, 在室履歴.入室時刻, 在室履歴.備考 
    ).outerjoin(教室, 在室履歴.教室ID == 教室.教室ID)\
     .filter(在室履歴.退室時刻 == None).all()
    
    # (在室中のIDだけSet型（高速な検索リスト）にしておく)
    active_student_ids = {s[0] for s in active_sessions_data}

    # --- ▼▼▼ 自動出席チェック機能 (ここから) ▼▼▼ ---
    if active_student_ids: # (在室者がいる場合のみチェック)
        try:
            now = datetime.now()
            today_str = f"{now.year}/{now.month}/{now.day}"
            
            # 2. 今が授業中か確認 (api_register_attendance から流用)
            plan_row = 授業計画.query.get(today_str) #
            if plan_row:
                kiki, yobi_code = plan_row.期, plan_row.授業曜日
                period_row = TimeTable.query.filter(
                    TimeTable.開始時刻 <= now.time(),
                    TimeTable.終了時刻 >= now.time()
                ).first() #
                
                if period_row:
                    current_period = period_row.時限
                    yobi_str = YOBI_MAP_REVERSE.get(yobi_code) #
                    class_row = 時間割.query.filter_by(
                        学期=str(kiki), 曜日=yobi_str, 時限=current_period
                    ).first() #
                    
                    if class_row and class_row.授業ID != 0:
                        # 3. 授業中だった場合
                        class_id = class_row.授業ID
                        today_date = now.date()

                        # 4.「今日・この時限・この授業」で「既に記録済み」の学生IDリストを取得
                        #   (検索対象を「在室中の学生」だけに絞る)
                        existing_records = db.session.query(出席記録.学生ID).filter(
                            出席記録.学生ID.in_(active_student_ids), 
                            出席記録.授業ID == class_id,
                            出席記録.時限 == current_period,
                            出席記録.出席日付 == today_date
                        ).all()
                        recorded_student_ids = {r[0] for r in existing_records}

                        # 5.「在室中」なのに「未記録」の学生を特定
                        students_to_mark = active_student_ids - recorded_student_ids
                        
                        new_records = []
                        for student_id in students_to_mark:
                            # (判定ロジックを使って、遅刻か出席かを自動判定)
                            status = 判定(current_period, now) 
                            
                            new_records.append(出席記録(
                                学生ID=student_id, 
                                授業ID=class_id, 
                                出席時刻=now,
                                状態=status, 
                                時限=current_period
                                # ⬅️ 出席日付は削除 (DBが勝手に計算してくれる)
                            ))
                        
                        if new_records:
                            db.session.add_all(new_records)
                            db.session.commit()
                            app.logger.info(f"【自動出席】{len(new_records)}件の記録を追加しました。")
                        
                            for record in new_records:
                                check_and_send_alert(record.学生ID, record.授業ID)

        except IntegrityError:
            db.session.rollback() 
            app.logger.warning("【自動出席エラー】IntegrityError、ロールバックしました。")
        except Exception as e:
            db.session.rollback()
            app.logger.error(f"【自動出席エラー】不明なエラー: {e}")
    
    all_students = 学生.query.all()
    
    # 教室名も取得 (LEFT JOIN)
    active_sessions = db.session.query(
        在室履歴.学生ID, 教室.教室名, 在室履歴.入室時刻, 在室履歴.備考 # ⬅️ 備考を追加
    ).outerjoin(教室, 在室履歴.教室ID == 教室.教室ID)\
     .filter(在室履歴.退室時刻 == None).all()

    now = datetime.now()
    active_map = {}
    for sid, room_name, 入室時刻, 備考 in active_sessions:
        try:
            滞在秒 = int((now - 入室時刻).total_seconds())
            hh = 滞在秒 // 3600
            mm = (滞在秒 % 3600) // 60
            ss = 滞在秒 % 60
            duration = f"{hh:02}:{mm:02}:{ss:02}"
            status = "一時退出中" if 備考 == "一時退出中" else "在室" # ⬅️ 備考でステータスを上書き
    
            active_map[sid] = {
                "status": status,  # ⬅️ 変更後のステータス
                "room": room_name or '教室不明', 
                "entry": 入室時刻.strftime("%Y-%m-%d %H:%M:%S"), 
                "duration": duration
            }
        except ValueError:
             active_map[sid] = (room_name or '教室不明', 入室時刻.strftime("%Y-%m-%d %H:%M:%S"), "Error")

    result = []
    for s in all_students:
        if s.学生ID in active_map:
            session_data = active_map.pop(s.学生ID)
            result.append({
                "name": s.学生名, 
                "status": session_data["status"], # ⬅️ status を取得
                "room": session_data["room"], 
                "entry": session_data["entry"], 
                "duration": session_data["duration"]
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
                check_and_send_alert(学生ID, 授業ID)
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

    python_weekday = datetime.now().weekday()
    db_weekday = (python_weekday + 1) % 7 # ⬅️ (例: 水=2 -> (2+1)%7 = 3)
    today_yobi = YOBI_MAP_REVERSE.get(db_weekday) # ⬅️ (例: 3 -> '水')

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
            elif status == "欠席": grouped_attendance[subject_name]["absent_count"] += 1 # ⬅️ これは DB上の欠席

    # 4. 出席率を計算
    report_data_summary = []
    for subject, data in grouped_attendance.items():
        total_classes_so_far = data["total_classes_so_far"] 
        if total_classes_so_far > 0:
            data["attendance_rate"] = round((data["attendance_count"] / total_classes_so_far) * 100, 1)
        else:
            data["attendance_rate"] = 0.0

        # 4b. 「未記録」を計算して「欠席」に合算
        total_recorded = data["attendance_count"] + data["tardy_count"] + data["absent_count"]
        unrecorded_count = total_classes_so_far - total_recorded
        if unrecorded_count < 0: unrecorded_count = 0
        
        total_absent = data["absent_count"] + unrecorded_count # ⬅️ 合算

        row = {
            "subject": subject,
            "attendance_rate": data["attendance_rate"],
            "total_classes": data["total_classes_planned"], 
            "total_classes_so_far": data["total_classes_so_far"], 
            "attendance_count": data["attendance_count"],
            "tardy_count": data["tardy_count"],
            "absent_count": total_absent, # ⬅️ 合算した値を渡す
        }
        report_data_summary.append(row)
        
    return render_template("my_attendance.html", 
                           student_id=student_id, student_name=student_name,
                           report_data=report_data_summary, selected_kiki=selected_kiki, 
                           kikis=["1", "2", "3", "4"])

@app.route("/my_attendance_detail", methods=["GET"])
@login_required 
def my_attendance_detail():
    """(レポート) 個人別出席詳細 (未記録も「欠席」として表示)"""
    student_id = request.args.get("student_id")
    selected_kiki = request.args.get("kiki", "1")
    subject_name_filter = request.args.get("subject")

    if not student_id or not student_id.isdigit():
        return redirect("/my_attendance")
    student_id = int(student_id)
    
    student_info = 学生.query.get(student_id)
    student_name = student_info.学生名 if student_info else "不明な学生"
    
    if not subject_name_filter:
        flash("詳細を表示する授業が指定されていません。", "error")
        return redirect(url_for('my_attendance', student_id=student_id, kiki=selected_kiki))

    kiki_int = int(selected_kiki)

    # 1. 授業名から授業IDを取得
    subject_obj = 授業.query.filter_by(授業科目名=subject_name_filter).first()
    if not subject_obj:
        flash(f"授業「{subject_name_filter}」が見つかりません。", "error")
        return redirect(url_for('my_attendance', student_id=student_id, kiki=selected_kiki))
    subject_id = subject_obj.授業ID

    # 2. (リスト1) DBに「実在する」出席記録を取得 (「時限」も取得)
    sql_records = text("""
        SELECT R."ID", R."出席時刻", R."状態", R."出席日付", R."時限"
        FROM "出席記録" R
        JOIN "授業計画" P ON R."出席日付" = TO_DATE(REPLACE(P."日付", '/', '-'), 'YYYY-MM-DD')
        WHERE R."学生ID" = :sid 
          AND R."授業ID" = :subject_id
          AND P."期" = :kiki_int
        ORDER BY R."出席日付", R."時限"
    """)
    actual_records_raw = db.session.execute(sql_records, {
        "sid": student_id, 
        "subject_id": subject_id,
        "kiki_int": kiki_int
    }).fetchall()
    
    # (記録済みの「(日付, 時限)」のタプルをSet型に保存)
    actual_tuples = {(record.出席日付.date(), record.時限) for record in actual_records_raw}

    # 3. (リスト2) この授業があった「昨日までの全日程・全時限」を取得
    
    # 3a. この授業の「曜日」と「時限」のペアを取得 (例: [('月', 1), ('月', 2), ('水', 1)])
    sql_schedule_slots = text("""
        SELECT T."曜日", T."時限" 
        FROM "時間割" T 
        WHERE T."授業ID" = :sid AND T."学期" = :kiki AND T."授業ID" != 0
    """)
    schedule_slots = db.session.execute(sql_schedule_slots, {"sid": subject_id, "kiki": selected_kiki}).fetchall()
    # (検索しやすいように 曜日 -> [時限リスト] の辞書に変換)
    schedule_map = {}
    for yobi, jigen in schedule_slots:
        if yobi not in schedule_map:
            schedule_map[yobi] = []
        schedule_map[yobi].append(jigen)

    # 3b. 授業計画から「昨日まで」の「日付」と「曜日コード」を取得
    planned_days_raw = []
    if schedule_map: # (スケジュールがある場合のみ)
        sql_planned_dates = text("""
            SELECT "日付", "授業曜日" 
            FROM "授業計画" 
            WHERE "期" = :kiki_int 
              AND "授業曜日" IN :day_codes
              AND TO_DATE(REPLACE("日付", '/', '-'), 'YYYY-MM-DD') <= CURRENT_DATE
        """)
        day_codes = [YOBI_MAP.get(name) for name in schedule_map.keys()] #
        planned_days_raw = db.session.execute(sql_planned_dates, {
            "kiki_int": kiki_int,
            "day_codes": tuple(day_codes)
        }).fetchall()

    # 4. (合体処理) 2つのリストを合体させる
    merged_report_list = []
    
    # 4a. まず「実在する記録」を全部入れる
    for record in actual_records_raw:
        merged_report_list.append({
            "record_id": record.ID,
            "timestamp": record.出席時刻, # datetimeオブジェクト
            "status": record.状態,
            "jigen": record.時限, # ⬅️ 時限を追加
            "is_phantom": False # 「幽霊」ではない (実在する)
        })
        
    # 4b. 次に「授業があった日」をチェック
    for date_row in planned_days_raw:
        try:
            planned_date = datetime.strptime(date_row[0], '%Y/%m/%d').date()
            yobi_code = date_row.授業曜日
            yobi_name = YOBI_MAP_REVERSE.get(yobi_code) #
        except (ValueError, TypeError):
            continue # 日付形式エラーや曜日不正はスキップ
            
        # (その曜日に予定されていた時限リストを取得)
        periods_for_this_day = schedule_map.get(yobi_name)
        if not periods_for_this_day:
            continue
            
        # (その日の時限を1つずつチェック)
        for jigen in periods_for_this_day:
            current_tuple = (planned_date, jigen)
            
            # (もし「授業があった(日, 時限)」が「記録済みリスト」になかったら)
            if current_tuple not in actual_tuples:
                # ＝ これが「未記録＝欠席」だ！
                
                # (ダミーの「×」データを作る)
                fake_timestamp = datetime.combine(planned_date, time(8, 50))
                
                merged_report_list.append({
                    "record_id": None,
                    "timestamp": fake_timestamp, 
                    "status": "欠席",
                    "jigen": jigen, # ⬅️ 時限を追加
                    "is_phantom": True
                })
            
    # 4c. 最終リストを日付順・時限順に並び替え
    merged_report_list.sort(key=lambda x: (x["timestamp"], x["jigen"]))

    # 5. データをHTMLで使いやすいように「仕分け」する
    report_data_detail = []
    status_map = {"出席": "○", "遅刻": "△", "欠席": "×"}
    
    if merged_report_list:
        row = {"subject": subject_name_filter}
        max_recorded_count = len(merged_report_list)
        
        for i in range(max_recorded_count):
            count_str = str(i + 1) 
            item = merged_report_list[i]
            
            formatted_date = item["timestamp"].strftime('%m/%d') 
            status_symbol = status_map.get(item["status"], item["status"])
            
            row[f"count_{count_str}_id"] = item["record_id"] if not item["is_phantom"] else f"phantom-{i}"
            row[f"count_{count_str}_status"] = status_symbol
            row[f"count_{count_str}_display"] = f"{status_symbol} ({formatted_date})"
            row[f"count_{count_str}_original_status"] = item["status"]
            row[f"count_{count_str}_is_phantom"] = item["is_phantom"]
            row[f"count_{count_str}_jigen"] = item["jigen"] # ⬅️ 時限をHTMLへ
        
        report_data_detail.append(row)
    else:
        max_recorded_count = 0 
        report_data_detail.append({"subject": subject_name_filter})

    # 6. HTMLテンプレートにデータを渡す
    return render_template("my_attendance_detail.html", 
                           student_id=student_id, student_name=student_name,
                           report_data=report_data_detail, max_count=max_recorded_count,
                           selected_kiki=selected_kiki, kikis=["1", "2", "3", "4"],
                           subject_filter=subject_name_filter,
                           is_portal_view=False 
                           )

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
                        JOIN "授業計画" P ON R."出席日付" = TO_DATE(REPLACE(P."日付", '/', '-'), 'YYYY-MM-DD')
                        WHERE R."授業ID" = :sid 
                          AND R."学生ID" = :stid 
                          AND P."期" = :kiki_int
                        GROUP BY R."状態"
                    """)
                    
                    # :kiki (文字列) ではなく :kiki_int (整数) を渡すように変更
                    counts = dict(db.session.execute(sql_counts, {
                        "sid": selected_subject_id, 
                        "stid": student.学生ID, 
                        "kiki_int": int(selected_kiki) 
                    }).fetchall())
                    
                    attended_count = counts.get('出席', 0)
                    tardy_count = counts.get('遅刻', 0)
                    absent_count_db = counts.get('欠席', 0) # 1. DBからの欠席
                    
                    attendance_rate = 0.0
                    if total_classes_so_far > 0:
                        attendance_rate = round((attended_count / total_classes_so_far) * 100, 1)
                    
                    total_recorded_so_far = attended_count + tardy_count + absent_count_db
                    unrecorded_count = total_classes_so_far - total_recorded_so_far
                    if unrecorded_count < 0: unrecorded_count = 0
                        
                    total_absent = absent_count_db + unrecorded_count
                    
                    summary.append({
                        'id': student.学生ID, 'name': student.学生名,
                        'max_count': max_count, 'attendance_rate': attendance_rate,
                        'total_classes_so_far': total_classes_so_far,
                        'counts': {
                            '出席': attended_count,
                            '遅刻': tardy_count,
                            '欠席': total_absent,   # ⬅️ ✔️ カンマをここに追加
                            'その他': 0
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
            sql_counts = text("""
                SELECT R."状態", COUNT(R."状態")
                FROM "出席記録" R
                JOIN "授業計画" P ON R."出席日付" = TO_DATE(REPLACE(P."日付", '/', '-'), 'YYYY-MM-DD')
                WHERE R."授業ID" = :sid 
                  AND R."学生ID" = :stid 
                  AND P."期" = :kiki_int
                GROUP BY R."状態"
            """)

            # :kiki (文字列) ではなく :kiki_int (整数) を渡すように変更
            counts = dict(db.session.execute(sql_counts, {
                "sid": selected_subject_id, 
                "stid": student.学生ID, 
                "kiki_int": int(selected_kiki)
            }).fetchall())
            
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
    """(メール送信) GAS経由のテストメール"""
    try:
        payload = {
            "to": os.environ.get('MAIL_USERNAME'), # 自分宛て
            "subject": "時間割情報のお知らせ（GASテスト）",
            "body": "これはGAS経由のテストメールです。\nRenderから送信されています。",
            "auth_token": os.environ.get('GAS_AUTH_TOKEN', 'YOUR_SECRET_GAS_TOKEN')
        }
        gas_url = os.environ.get('GAS_API_URL')
        
        if gas_url:
            requests.post(gas_url, json=payload)
            flash("✅ GAS経由でテストメールを送信しました。", "success")
        else:
            flash("❌ GAS_API_URLが設定されていません。", "error")
            
    except Exception as e:
        flash(f"❌ 送信エラー: {e}", "error")
        
    return redirect(url_for('index'))

@app.route("/alerts")
@login_required
def alerts():
    """(管理) 遅刻・欠席の連絡掲示板"""
    
    # 未解決の連絡を最新のものから取得
    unresolved_reports = db.session.query(ReportRecord, 学生.学生名) \
        .join(学生, ReportRecord.student_id == 学生.学生ID) \
        .filter(ReportRecord.is_resolved == False) \
        .order_by(ReportRecord.report_date.desc()) \
        .all()
        
    return render_template("alerts.html", reports=unresolved_reports)

@app.route("/resolve_alert/<int:record_id>", methods=["POST"])
@login_required
def resolve_alert(record_id):
    """連絡を管理者確認済みにする"""
    report = ReportRecord.query.get(record_id)
    if report:
        report.is_resolved = True
        db.session.commit()
        flash("✅ 連絡を確認済みにしました。", "success")
    return redirect(url_for('alerts'))

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
        now = datetime.now()
        quick_reply_buttons = QuickReply(items=[
            QuickReplyButton(action=MessageAction(label="今日の時間割", text="今日の時間割")),
            QuickReplyButton(action=MessageAction(label="明日の時間割", text="明日の時間割")),
            QuickReplyButton(action=MessageAction(label="出席サマリー", text="出席サマリー")),
            
            # 👇 新しく追加する一時退出のボタン
            QuickReplyButton(action=MessageAction(label="一時退出", text="一時退出")),
            QuickReplyButton(action=MessageAction(label="戻りました", text="戻りました")),
            
            # 👇 最終退室のボタン（既存の「退室」ボタンを明確化）
            QuickReplyButton(action=MessageAction(label="最終退室", text="最終退室")), 
        ])
        # --- 1. アカウント登録処理 ---
        if received_text.startswith("登録"):
            try:
                input_student_id = int(received_text.split(":")[1].strip())
            except (IndexError, ValueError):
                return line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 登録形式が正しくありません。「登録:学生ID」の形式で入力してください。"))

            student = 学生.query.get(input_student_id)
            if not student:
                return line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 学生ID {input_student_id} はデータベースに存在しません。"))
            
            # 既存の紐付けをチェック
            existing_mapping = LineUser.query.filter_by(line_user_id=user_id).first()
            if existing_mapping:
                existing_mapping.student_id = input_student_id
                db.session.commit()
                return line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ 登録情報を更新しました。\nあなたのID ({input_student_id}) が紐づきました。"))

            # 新しい紐付けを登録
            new_mapping = LineUser(line_user_id=user_id, student_id=input_student_id)
            db.session.add(new_mapping)
            db.session.commit()
            return line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🎉 登録が完了しました！\nあなたのID ({input_student_id}) がBotに紐づきました。"))
        
        # --- 2. 遅刻/欠席の連絡処理 ---
        if received_text.startswith("欠席連絡") or received_text.startswith("遅刻連絡:"):
    
            # ユーザーの学生IDを取得
            student_id = get_student_id_from_line_user(user_id)
            if student_id is None:
                return line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ 登録がされていません。\n「登録:学生ID」で紐付けてください。"))

            report_type = "欠席" if received_text.startswith("欠席連絡") else "遅刻"
            try:
                reason = received_text.split(":", 1)[1].strip()
                if not reason: raise IndexError
            except IndexError:
                return line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ {report_type}連絡の理由を必ず記述してください。\n例: 「{report_type}連絡:腹痛のため」"))
            
            # データベースに記録
            new_report = ReportRecord(
                student_id=student_id,
                report_type=report_type,
                reason=reason,
                report_date=datetime.now(),
                is_resolved=False
            )
            db.session.add(new_report)
            db.session.commit()
            
            student = 学生.query.get(student_id)
            return line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text=f"📢 {student.学生名}さん、{report_type}連絡を承りました。\n理由: {reason}\n管理者へ通知します。"
            ))

        if received_text == "今日の時間割" or received_text == "明日の時間割":
            days_ahead = 0 if received_text == "今日の時間割" else 1
            target_date = now + timedelta(days=days_ahead)
            
            # (休日判定)
            if target_date.weekday() >= 5: # 土日
                reply_message_text = f"📅 {target_date.strftime('%Y/%m/%d')} は土日祝日のため、授業はありません。"
                # 土日の場合はテキストで返信
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text=reply_message_text, quick_reply=quick_reply_buttons)
                )
                return # ★ここで処理を終了

            else:
                # ▼▼▼ ここが変更点 ▼▼▼
                # get_schedule_for_line が BubbleContainer または 文字列 を返す
                schedule_data = get_schedule_for_line(target_date) 
                
                if isinstance(schedule_data, BubbleContainer):
                    # 戻り値が BubbleContainer だったら FlexSendMessage で送信
                    reply_message = FlexSendMessage(
                        alt_text=f"{target_date.strftime('%Y/%m/%d')}の時間割",
                        contents=schedule_data, # ここにBubbleを入れる
                        quick_reply=quick_reply_buttons # FlexにもQuickReplyは付けられる
                    )
                else:
                    # 戻り値が 文字列 だったら (エラー時) TextSendMessage で送信
                    reply_message = TextSendMessage(
                        text=schedule_data, # (例: "授業計画が見つかりません。")
                        quick_reply=quick_reply_buttons
                    )
                
                line_bot_api.reply_message(event.reply_token, reply_message)
                return # ★ここで処理を終了
            
        elif received_text == "出席サマリー":
            summary_data = get_attendance_summary_for_line(user_id) 
            
            if isinstance(summary_data, BubbleContainer):
                # 戻り値が BubbleContainer だったら FlexSendMessage で送信
                reply_message_obj = FlexSendMessage(
                    alt_text=f"出席サマリー",
                    contents=summary_data, # ここにBubbleを入れる
                    quick_reply=quick_reply_buttons # QuickReplyも付ける
                )
            else:
                # 戻り値が 文字列 だったら (エラー時) TextSendMessage で送信
                reply_message_obj = TextSendMessage(
                    text=summary_data, # (例: "学生IDが登録されていません。")
                    quick_reply=quick_reply_buttons
                )
            
            line_bot_api.reply_message(event.reply_token, reply_message_obj)
            return # ★ここで処理を終了
            
        elif received_text == "退室":
            # 在室履歴を終了させるロジック
            reply_message = process_exit_record(user_id) # ⚠️ 新しいヘルパー関数を定義します
        
        elif received_text == "気温":
            if sensor_data:
                 latest = sensor_data[-1]
                 reply_message = f"現在の気温は {latest.get('temperature')}℃ です。"
            else:
                 reply_message = "センサーデータがまだありません。"
            # 🚨 一時退出
        elif received_text == "一時退出":
            # process_temporary_exit は学生IDを紐付けテーブルから取得して処理を実行
            reply_message = process_temporary_exit(user_id) 
            
        # 🚨 復帰
        elif received_text == "戻りました":
            reply_message = process_return_from_exit(user_id)
            
        # 🚨 最終退室
        elif received_text == "最終退室":
            # process_exit_record は退室時刻を記録してセッションを終了
            reply_message = process_exit_record(user_id)
            
        # 🚨 以前の "退室" コマンドも一応残しておく（クイックリプライでは「最終退室」を使う）
        elif received_text == "退室":
            reply_message = process_exit_record(user_id)         
        else:
            reply_message = f"「{received_text}」を受け取りました。"

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_message, quick_reply=quick_reply_buttons)
        )

# ----------------------------------------------------------------------
# 11. 学生専用ポータル (Student Portal)
# ----------------------------------------------------------------------
@app.route("/student_register", methods=["GET", "POST"])
def student_register():
    """学生専用の初回パスワード設定ページ"""
    if current_user.is_authenticated:
        return redirect(url_for('my_portal')) # ログイン済ならポータルへ

    if request.method == "POST":
        try:
            student_id = int(request.form.get("student_id"))
            password = request.form.get("password")
            password_confirm = request.form.get("password_confirm")

            if not student_id or not password or not password_confirm:
                flash("❌ すべての項目を入力してください。", "error")
                return redirect(url_for('student_register'))
            
            if password != password_confirm:
                flash("❌ パスワードが一致しません。", "error")
                return redirect(url_for('student_register'))

            student = 学生.query.get(student_id)
            
            if not student:
                flash("❌ その学生IDは存在しません。管理者に確認してください。", "error")
                return redirect(url_for('student_register'))
            
            # 🚨 ここが重要: すでにパスワードが設定済みかチェック
            if student.password_hash is not None:
                flash("⚠️ この学生IDは既にパスワード設定済みです。ログイン画面からログインしてください。", "warning")
                return redirect(url_for('student_login'))
            
            # パスワードをハッシュ化して設定
            student.set_password(password)
            db.session.commit()
            
            flash("✅ パスワードを設定しました！ ログインしてください。", "success")
            return redirect(url_for('student_login'))

        except ValueError:
            flash("学生IDは数字で入力してください。", "error")
        except Exception as e:
            db.session.rollback()
            flash(f"登録エラーが発生しました: {e}", "error")
            
    return render_template("student_register.html")

@app.route("/student_login", methods=["GET", "POST"])
def student_login():
    """学生専用のログインページ"""
    if current_user.is_authenticated:
        # すでにログイン済みの場合
        if current_user.get_id().startswith('student-'):
            return redirect(url_for('my_portal'))
        else:
            return redirect(url_for('index')) # 管理者は管理画面へ

    if request.method == "POST":
        try:
            student_id = int(request.form.get("student_id"))
            password = request.form.get("password")
            
            student = 学生.query.get(student_id)
            
            # データベースにパスワードが設定されているか、ハッシュで一致するかを確認
            if student and student.check_password(password):
                login_user(student) # ⬅️ 学生としてログイン
                return redirect(url_for('my_portal'))
            else:
                flash("学生IDまたはパスワードが間違っています。", "error")
                
        except ValueError:
            flash("学生IDは数字で入力してください。", "error")
        except Exception as e:
            flash(f"ログインエラーが発生しました: {e}", "error")
            
    return render_template("student_login.html")


@app.route("/my_portal")
@login_required # ログイン必須
def my_portal():
    """学生専用ポータル (自分の情報だけ表示)"""
    
    # ログイン中のユーザーが学生かどうかをIDのプレフィックスで確認
    if not current_user.get_id().startswith('student-'):
        # もし管理者がアクセスしようとしたら、管理トップに強制移動
        flash("管理者はこのページにアクセスできません。", "error")
        return redirect(url_for('index'))
    
    # current_user は、load_userによって 学生 オブジェクトになっている
    student_id = current_user.学生ID
    student_name = current_user.学生名
    
    # ------------------------------------------------------------------
    # ▼▼▼ 時間割・サマリーデータ取得ロジック ▼▼▼
    # ------------------------------------------------------------------

    selected_kiki = get_current_kiki()
    kiki_int = int(selected_kiki)
    
    # --- ▼▼▼ (ここから修正) LINE Botの授業ごとサマリー計算ロジックを移植 ▼▼▼ ---
    
    # 1. 履修科目の一覧を取得
    sql_enrolled = text("""
        SELECT DISTINCT S."授業科目名", S."授業ID"
        FROM "時間割" T
        JOIN "授業" S ON T."授業ID" = S."授業ID"
        WHERE T."学期" = :kiki AND T."授業ID" != 0 
        ORDER BY S."授業科目名"
    """) #
    enrolled_subjects = db.session.execute(sql_enrolled, {"kiki": selected_kiki}).fetchall()

    report_data = [] # ⬅️ 空のリストをまず定義

    if enrolled_subjects:
        # 2. 各授業の出席データを集計
        for subject_name, subject_id in enrolled_subjects:
            
            # 2a. その授業の「今日までの総コマ数」を計算
            sql_schedule = text('SELECT T."曜日", COUNT(T."時限") FROM "時間割" T WHERE T."授業ID" = :sid AND T."学期" = :kiki GROUP BY T."曜日"')
            schedule_data = db.session.execute(sql_schedule, {"sid": subject_id, "kiki": selected_kiki}).fetchall()
            
            total_classes_so_far = 0
            for day_of_week, periods_per_day in schedule_data:
                day_code = YOBI_MAP.get(day_of_week) #
                if day_code is not None:
                    sql_days_so_far = text("""
                        SELECT COUNT("日付") FROM "授業計画" 
                        WHERE "期" = :kiki AND "授業曜日" = :code 
                        AND TO_DATE(REPLACE("日付", '/', '-'), 'YYYY-MM-DD') <= CURRENT_DATE
                    """) #
                    total_days_so_far = db.session.execute(sql_days_so_far, {"kiki": kiki_int, "code": day_code}).scalar()
                    total_classes_so_far += total_days_so_far * periods_per_day
            
            # 2b. その授業の「出席記録」を集計
            sql_records = text("""
                SELECT R."状態", COUNT(R."状態")
                FROM "出席記録" R
                JOIN "授業計画" P ON R."出席日付" = TO_DATE(REPLACE(P."日付", '/', '-'), 'YYYY-MM-DD')
                WHERE R."学生ID" = :sid 
                  AND P."期" = :kiki_int
                  AND R."授業ID" = :subject_id
                GROUP BY R."状態"
            """)
            records_count = dict(db.session.execute(sql_records, {
                "sid": student_id, 
                "kiki_int": kiki_int,
                "subject_id": subject_id
            }).fetchall())

            attendance_count = records_count.get('出席', 0)
            tardy_count = records_count.get('遅刻', 0)
            absent_count_db = records_count.get('欠席', 0) # ⬅️ DB上の欠席

            # 2c. 出席率を計算
            attendance_rate = 0.0
            if total_classes_so_far > 0:
                attendance_rate = round((attendance_count / total_classes_so_far) * 100, 1)
            
            # 2d. 「未記録」を計算して「欠席」に合算
            total_recorded = attendance_count + tardy_count + absent_count_db
            unrecorded_count = total_classes_so_far - total_recorded
            if unrecorded_count < 0: unrecorded_count = 0

            total_absent = absent_count_db + unrecorded_count # ⬅️ 合算

            # 2e. データをリストに追加
            report_data.append({
                "subject": subject_name,
                "attendance_rate": attendance_rate,
                "total_classes_so_far": total_classes_so_far, 
                "attendance_count": attendance_count,
                "tardy_count": tardy_count,
                "absent_count": total_absent # ⬅️ 合算した値を渡す
            })
    
    # --- 2. 時間割データを取得 ---
    # 授業と教室をJOINして取得
    schedules_rows = db.session.query(
        時間割.時間割ID, 時間割.曜日, 時間割.時限, 時間割.学期, 
        授業.授業科目名, 授業.担当教員, 教室.教室名, 
        時間割.備考, 時間割.授業ID
    ).outerjoin(授業, 時間割.授業ID == 授業.授業ID)\
     .outerjoin(教室, 授業.教室ID == 教室.教室ID)\
     .filter(時間割.学期 == selected_kiki)\
     .order_by(時間割.時限, 時間割.曜日).all()

    順序 = ["月", "火", "水", "木", "金"] # テンプレートで使う曜日のリスト
    時限一覧 = list(range(1, 6))
    schedule_grid = OrderedDict()
    
    # 曜日のヘッダー表示のために、まず空のグリッドを作成
    for j in 時限一覧:
        schedule_grid[j] = {y: {"is_empty": True, "remark": None, "teacher": None, "room": None, "display_text": "休憩/空欄"} for y in 順序}
        
    for row in schedules_rows:
        時間割ID, 曜日, 時限, 学期, 授業科目名, 担当教員, 教室名, 備考, 授業ID = row 
        if 時限 in 時限一覧 and 曜日 in 順序:
            教員名 = 担当教員 if 担当教員 else '教員不明'
            表示用教室名 = 教室名 if 教室名 else '教室不明'
            display_name = 備考 if 時限 == 5 else (授業科目名 if 授業科目名 else "授業名不明")
            # 5限で備考がない、または5限以外で授業IDがない場合は空欄扱い
            is_empty = (not 授業ID and not 備考) if (時限 < 5 or (時限 == 5 and not 備考)) else (時限 == 5 and not 備考)
            
            schedule_grid[時限][曜日] = {
                "id": 時間割ID, "subject": 授業科目名, "teacher": 教員名,
                "room": 表示用教室名, "display_text": display_name,
                "subject_id": 授業ID if 授業ID else 0,
                "remark": 備考, "is_empty": is_empty
            }

    python_weekday = datetime.now().weekday()
    db_weekday = (python_weekday + 1) % 7 # ⬅️ (例: 水=2 -> (2+1)%7 = 3)
    today_yobi = YOBI_MAP_REVERSE.get(db_weekday) # ⬅️ (例: 3 -> '水')

    # ------------------------------------------------------------------
    # ▲▲▲ データ取得ロジックここまで ▲▲▲
    # ------------------------------------------------------------------

    return render_template("my_portal.html", 
                           student_name=student_name,
                           report_data=report_data, 
                           selected_kiki=selected_kiki,
                           schedule_grid=schedule_grid,
                           曜日順=順序,
                           時限一覧=時限一覧,
                           today_yobi=today_yobi
                           )

@app.route("/update_parent_email", methods=["POST"])
@login_required
def update_parent_email():
    """学生が自分の保護者メアドを更新する処理"""
    
    # 学生以外は弾く
    if not current_user.get_id().startswith('student-'):
        return redirect(url_for('index'))
    
    parent_email = request.form.get("parent_email")
    
    # データベースを更新
    try:
        # current_user はログイン中の学生データそのもの
        student = 学生.query.get(current_user.学生ID)
        student.parent_email = parent_email
        db.session.commit()
        flash("✅ 保護者のメールアドレスを保存しました。", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"❌ 保存に失敗しました: {e}", "error")
        
    return redirect(url_for('my_portal'))

@app.route("/my_portal_detail")
@login_required #
def my_portal_detail():
    """ (新機能) 学生専用ポータル - 出席詳細 (未記録も「欠席」として表示) """

    if not current_user.get_id().startswith('student-'):
        flash("管理者はこのページにアクセスできません。", "error")
        return redirect(url_for('index'))
    
    # --- ▼▼▼ 修正点1: IDを自分自身に固定 ▼▼▼ ---
    student_id = current_user.学生ID
    student_name = current_user.学生名
    
    selected_kiki = request.args.get("kiki", "1")
    subject_name_filter = request.args.get("subject")

    if not subject_name_filter:
        flash("詳細を表示する授業が指定されていません。", "error")
        return redirect(url_for('my_portal'))

    kiki_int = int(selected_kiki)

    # --- ▼▼▼ (ここから /my_attendance_detail と全く同じロジック) ▼▼▼ ---
    
    # 1. 授業名から授業IDを取得
    subject_obj = 授業.query.filter_by(授業科目名=subject_name_filter).first()
    if not subject_obj:
        flash(f"授業「{subject_name_filter}」が見つかりません。", "error")
        return redirect(url_for('my_portal'))
    subject_id = subject_obj.授業ID

    # 2. (リスト1) DBに「実在する」出席記録を取得 (「時限」も取得)
    sql_records = text("""
        SELECT R."ID", R."出席時刻", R."状態", R."出席日付", R."時限"
        FROM "出席記録" R
        JOIN "授業計画" P ON R."出席日付" = TO_DATE(REPLACE(P."日付", '/', '-'), 'YYYY-MM-DD')
        WHERE R."学生ID" = :sid 
          AND R."授業ID" = :subject_id
          AND P."期" = :kiki_int
        ORDER BY R."出席日付", R."時限"
    """)
    actual_records_raw = db.session.execute(sql_records, {
        "sid": student_id, # ⬅️ (自分のIDが使われる)
        "subject_id": subject_id,
        "kiki_int": kiki_int
    }).fetchall()
    
    actual_tuples = {(record.出席日付.date(), record.時限) for record in actual_records_raw}

    # 3. (リスト2) この授業があった「昨日までの全日程・全時限」を取得
    sql_schedule_slots = text("""
        SELECT T."曜日", T."時限" 
        FROM "時間割" T 
        WHERE T."授業ID" = :sid AND T."学期" = :kiki AND T."授業ID" != 0
    """)
    schedule_slots = db.session.execute(sql_schedule_slots, {"sid": subject_id, "kiki": selected_kiki}).fetchall()
    schedule_map = {}
    for yobi, jigen in schedule_slots:
        if yobi not in schedule_map:
            schedule_map[yobi] = []
        schedule_map[yobi].append(jigen)

    planned_days_raw = []
    if schedule_map:
        sql_planned_dates = text("""
            SELECT "日付", "授業曜日" 
            FROM "授業計画" 
            WHERE "期" = :kiki_int 
              AND "授業曜日" IN :day_codes
              AND TO_DATE(REPLACE("日付", '/', '-'), 'YYYY-MM-DD') <= CURRENT_DATE
        """)
        day_codes = [YOBI_MAP.get(name) for name in schedule_map.keys()]
        planned_days_raw = db.session.execute(sql_planned_dates, {
            "kiki_int": kiki_int,
            "day_codes": tuple(day_codes)
        }).fetchall()

    # 4. (合体処理) 2つのリストを合体させる
    merged_report_list = []
    
    for record in actual_records_raw:
        merged_report_list.append({
            "record_id": record.ID, "timestamp": record.出席時刻,
            "status": record.状態, "jigen": record.時限, "is_phantom": False
        })
        
    for date_row in planned_days_raw:
        try:
            planned_date = datetime.strptime(date_row[0], '%Y/%m/%d').date()
            yobi_code = date_row.授業曜日
            yobi_name = YOBI_MAP_REVERSE.get(yobi_code)
        except (ValueError, TypeError):
            continue
            
        periods_for_this_day = schedule_map.get(yobi_name)
        if not periods_for_this_day:
            continue
            
        for jigen in periods_for_this_day:
            current_tuple = (planned_date, jigen)
            if current_tuple not in actual_tuples:
                fake_timestamp = datetime.combine(planned_date, time(8, 50))
                merged_report_list.append({
                    "record_id": None, "timestamp": fake_timestamp, 
                    "status": "欠席", "jigen": jigen, "is_phantom": True
                })
            
    merged_report_list.sort(key=lambda x: (x["timestamp"], x["jigen"]))

    # 5. データをHTMLで使いやすいように「仕分け」する
    report_data_detail = []
    status_map = {"出席": "○", "遅刻": "△", "欠席": "×"}
    
    if merged_report_list:
        row = {"subject": subject_name_filter}
        max_recorded_count = len(merged_report_list)
        
        for i in range(max_recorded_count):
            count_str = str(i + 1) 
            item = merged_report_list[i]
            
            formatted_date = item["timestamp"].strftime('%m/%d') 
            status_symbol = status_map.get(item["status"], item["status"])
            
            row[f"count_{count_str}_id"] = item["record_id"] if not item["is_phantom"] else f"phantom-{i}"
            row[f"count_{count_str}_status"] = status_symbol
            row[f"count_{count_str}_display"] = f"{status_symbol} ({formatted_date})"
            row[f"count_{count_str}_original_status"] = item["status"]
            row[f"count_{count_str}_is_phantom"] = item["is_phantom"]
            row[f"count_{count_str}_jigen"] = item["jigen"] # ⬅️ 時限をHTMLへ
        
        report_data_detail.append(row)
    else:
        max_recorded_count = 0 
        report_data_detail.append({"subject": subject_name_filter})

    # --- ▲▲▲ (ロジックはここまで同じ) ▲▲▲ ---

    # 6. HTMLテンプレートにデータを渡す
    return render_template("my_attendance_detail.html", 
                           student_id=student_id, 
                           student_name=student_name,
                           report_data=report_data_detail, 
                           max_count=max_recorded_count,
                           selected_kiki=selected_kiki, 
                           kikis=["1", "2", "3", "4"],
                           subject_filter=subject_name_filter,
                           # --- ▼▼▼ 修正点2: ポータルビューフラグをTrueに ▼▼▼ ---
                           is_portal_view=True 
                           )
    
@app.route("/student_logout")
@login_required
def student_logout():
    """学生専用ログアウト"""
    logout_user()
    flash("ログアウトしました。", "success")
    return redirect(url_for('student_login'))

@app.route("/api/trigger_remote_auth", methods=["POST"])
@login_required
def api_trigger_remote_auth():
    student_id = current_user.学生ID
    # 命令をセット
    auth_commands[str(student_id)] = "START"
    print(f"🔔 [Web->Server] ID:{student_id} にカメラ起動命令をセットしました")
    return jsonify({"status": "success"})

# ② Python(PC) -> Server: 「僕への命令ある？」と確認する (ポーリング)
@app.route("/api/poll_command", methods=["GET"])
def api_poll_command():
    student_id = request.args.get("student_id")
    # 命令を取り出して消す（1回限り実行させるため pop を使用）
    command = auth_commands.pop(str(student_id), None)
    
    if command:
        print(f"📤 [Server->Python] ID:{student_id} に命令 {command} を渡しました")
        
    return jsonify({"command": command})

# ③ Python(PC) -> Server: 「認証成功したよ！」と報告 & 出席登録
# app.py の api_report_remote_result をこれに書き換え

@app.route("/api/report_remote_result", methods=["POST"])
def api_report_remote_result():
    data = request.get_json()
    student_id = data.get("student_id")
    result = data.get("result") # "SUCCESS"
    
    if result == "SUCCESS":
        print(f"✅ [報告受信] ID:{student_id} 認証成功")
        
        # --- 出席登録ロジック ---
        now = datetime.now()
        target_period = None
        
        # 1. 今の時間の授業を探す (前後20分の余裕)
        all_periods = TimeTable.query.all()
        for p in all_periods:
            p_start = datetime.combine(now.date(), p.開始時刻)
            p_end = datetime.combine(now.date(), p.終了時刻)
            if (p_start - timedelta(minutes=20)) <= now <= (p_end + timedelta(minutes=20)):
                target_period = p.時限
                break
        
        if target_period:
            today_yobi_str = YOBI_MAP_REVERSE.get((now.weekday() + 1) % 7)
            kiki = get_current_kiki()
            class_row = 時間割.query.filter_by(学期=kiki, 曜日=today_yobi_str, 時限=target_period).first()
            
            if class_row:
                subject_id = class_row.授業ID
                
                # ★★★ 修正ポイント: ここで遅刻・欠席を判定する ★★★
                status = 判定(target_period, now)
                
                # 重複チェック
                existing = 出席記録.query.filter_by(学生ID=student_id, 授業ID=subject_id, 出席日付=now.date(), 時限=target_period).first()
                
                if not existing:
                    new_attendance = 出席記録(
                        学生ID=student_id, 
                        授業ID=subject_id, 
                        出席時刻=now, 
                        状態=status, # 👈 判定結果("遅刻"や"欠席")を入れる
                        時限=target_period
                    )
                    db.session.add(new_attendance)
                    db.session.commit()
                    
                    # アラートが必要ならチェック
                    check_and_send_alert(student_id, subject_id)
                    
                    print(f"   -> DBに「{status}」で登録しました")
                else:
                    # 既に登録済みなら、在席確認として時刻だけ更新（状態は変えない）
                    existing.出席時刻 = now
                    db.session.commit()

        # Web側に完了を知らせるフラグをセット
        auth_commands[f"RESULT_{student_id}"] = "SUCCESS"
        return jsonify({"status": "received"})
    
    return jsonify({"status": "ignored"})

# ④ Web(学生) -> Server: 「結果きた？」と確認する
@app.route("/api/check_remote_result", methods=["GET"])
@login_required
def api_check_remote_result():
    student_id = current_user.学生ID
    # 結果を取り出す
    result = auth_commands.pop(f"RESULT_{student_id}", None)
    
    if result == "SUCCESS":
        return jsonify({"status": "success"})
    else:
        return jsonify({"status": "waiting"})

@app.route('/api/upload_image', methods=['POST'])
def upload_image():
    student_id = request.form.get('student_id')
    image_file = request.files.get('file') # .getを使うとエラーになりにくい
    
    if image_file and student_id:
        # ファイル名を安全なものに変換 (例: "../taro.jpg" -> "taro.jpg")
        safe_filename = secure_filename(image_file.filename)
        
        # ID_ファイル名 の形式にする
        save_name = f"{student_id}_{safe_filename}"
        save_path = os.path.join(UPLOAD_DIR, save_name)
        
        try:
            image_file.save(save_path)
            print(f"📸 画像が保存されました: {save_path}")
            return 'Image uploaded successfully', 200
        except Exception as e:
            print(f"保存エラー: {e}")
            return f'Error saving file: {e}', 500
            
    return 'No file or student_id uploaded', 400

# --- 12. 実行 ---
if __name__ == "__main__":
    with app.app_context():
        pass
    is_debug = os.environ.get('FLASK_DEBUG', 'False') == 'True'
    app.run(host="0.0.0.0", port=5000, threaded=True, debug=is_debug)
