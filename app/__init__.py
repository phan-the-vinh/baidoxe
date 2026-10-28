import os
from datetime import timedelta
from flask import Flask, jsonify, render_template, request
from dotenv import load_dotenv
from flask_wtf.csrf import CSRFError
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from .config import ROOT, database_url
from .extensions import db, login_manager, csrf
from .services.common import BusinessError, local_time, today

def create_app(test_config=None):
    load_dotenv(ROOT / '.env')
    application = Flask(__name__, instance_path=str(ROOT/'instance'))
    test_mode = bool(test_config and test_config.get('TESTING'))
    secret = os.getenv('SECRET_KEY', '')
    if not test_mode and len(secret) < 32:
        raise RuntimeError('SECRET_KEY chưa có hoặc quá ngắn. Chạy python tools/setup_env.py trước.')
    application.config.update(SECRET_KEY=secret,
        SQLALCHEMY_DATABASE_URI=None if test_mode else database_url(),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS={'pool_pre_ping': True},
        MAX_CONTENT_LENGTH=30*1024*1024,
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8), WTF_CSRF_TIME_LIMIT=8*3600)
    if test_config:
        application.config.update(test_config)
    db.init_app(application)
    login_manager.init_app(application)

    # Flask-WTF reads request.form in its before_request hook even when the
    # token is sent in X-CSRFToken. Set the route-specific multipart limit
    # before CSRF parses the body; assigning it inside the view is too late.
    @application.before_request
    def vision_video_request_limit():
        if request.endpoint == 'api.api_vision_video_create':
            from .services.vision_video import MAX_VIDEO_BYTES
            request.max_content_length = MAX_VIDEO_BYTES + 2 * 1024 * 1024
        elif request.endpoint == 'api.api_vision_plate':
            from .services.plate_detection import MAX_PLATE_FRAME_WINDOW_BYTES
            request.max_content_length = MAX_PLATE_FRAME_WINDOW_BYTES

    csrf.init_app(application)
    login_manager.login_view = 'web.login'
    login_manager.login_message = 'Hãy đăng nhập để tiếp tục.'
    @login_manager.unauthorized_handler
    def unauthorized():
        from flask import redirect, url_for
        if request.path.startswith('/api/'):
            return jsonify(error='Phiên làm việc đã hết hạn. Hãy đăng nhập lại.'), 401
        return redirect(url_for('web.login'))
    from .models import User
    @login_manager.user_loader
    def load_user(user_id):
        try:
            return db.session.get(User, int(user_id))
        except (ValueError, TypeError):
            return None
    from .routes import web, api
    from .cli import register_cli
    application.register_blueprint(web)
    application.register_blueprint(api)
    register_cli(application)
    application.jinja_env.filters['money'] = lambda v: f'{int(v or 0):,}'.replace(',', '.') + ' đ'
    application.jinja_env.filters['localdt'] = lambda v: local_time(v).strftime('%d/%m/%Y %H:%M:%S') if v else '—'
    application.context_processor(lambda: {
        'today': today(),
        'is_test_environment': application.testing,
        'test_environment_label': application.config.get(
            'TEST_ENVIRONMENT_LABEL', 'Môi trường kiểm thử tách biệt'
        ),
    })

    def error_response(message, status):
        if request.path.startswith('/api/'):
            return jsonify(error=message), status
        return render_template('error.html', message=message, status=status), status

    @application.errorhandler(BusinessError)
    def business_error(exc):
        db.session.rollback()
        return error_response(str(exc), exc.status)

    @application.errorhandler(CSRFError)
    def csrf_error(exc):
        return error_response('Phiên xác nhận đã hết hạn. Tải lại trang và thử lại.', 400)

    @application.errorhandler(IntegrityError)
    def conflict(exc):
        db.session.rollback()
        return error_response('Dữ liệu bị trùng hoặc đã thay đổi. Tải lại để kiểm tra.', 409)

    @application.errorhandler(SQLAlchemyError)
    def database_error(exc):
        db.session.rollback()
        # Khong tra/log chuoi ket noi, SQL parameters, mat khau.
        application.logger.error('Database failure: %s', type(exc).__name__)
        return error_response('Không thể xử lý CSDL. Kiểm tra SQL Server/ODBC và thử lại; thao tác chưa được hoàn tất.', 503)

    @application.errorhandler(403)
    @application.errorhandler(404)
    @application.errorhandler(405)
    @application.errorhandler(413)
    def http_error(exc):
        messages = {403:'Bạn không có quyền sử dụng chức năng này.', 404:'Không tìm thấy nội dung.',
                    405:'Phương thức không được hỗ trợ.', 413:'Tệp vượt quá giới hạn 30 MB.'}
        if exc.code == 413 and request.path == '/api/vision/video':
            messages[413] = 'Video vượt quá giới hạn 250 MB.'
        return error_response(messages[exc.code], exc.code)

    @application.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        if not request.path.startswith('/static/'):
            response.headers['Cache-Control'] = 'no-store'
        return response
    return application
