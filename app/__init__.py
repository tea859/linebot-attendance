import os
from flask import Flask
from .extensions import db, login_manager, mail
from .models import 学生, User # Userクラスもmodels.pyに移した前提です
from .services import admin_user_db

def create_app():
    app = Flask(__name__)

    # --- 設定読み込み ---
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY')
    database_url = os.environ.get('DATABASE_URL', 'sqlite:///zaiseki.db')
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    
    # Mail設定
    app.config['MAIL_SERVER'] = 'smtp.gmail.com'
    app.config['MAIL_PORT'] = 587
    app.config['MAIL_USE_TLS'] = True
    app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
    app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')

    # --- 拡張機能の初期化 ---
    db.init_app(app)
    login_manager.init_app(app)
    mail.init_app(app)

    # ログイン画面の指定
    login_manager.login_view = 'web.login'
    login_manager.login_message = "このページにアクセスするにはログインが必要です。"
    login_manager.login_message_category = "error"

    # --- ユーザー読み込みロジック (ここに入れます！) ---
    @login_manager.user_loader
    def load_user(user_id):
        # user_id は 'admin-1' または 'student-222521301' の形式
        
        if user_id.startswith('admin-'):
            # 管理者ユーザーを読み込む
            try:
                parts = user_id.split('-')
                if len(parts) > 1:
                    admin_id = parts[1]
                    return admin_user_db.get(admin_id)
            except:
                return None
            
        elif user_id.startswith('student-'):
            # 学生ユーザーを読み込む
            try:
                parts = user_id.split('-')
                if len(parts) > 1:
                    student_id = int(parts[1])
                    return 学生.query.get(student_id)
            except:
                return None
        return None

    # --- Blueprintの登録 ---
    from .routes.web import web_bp
    from .routes.api import api_bp
    from .routes.line import line_bp

    app.register_blueprint(web_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(line_bp)

    return app