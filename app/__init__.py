"""Flask application factory and cross-cutting app wiring."""

import os

# 第三方库
import redis
from flask import Flask, session
from flask_session import Session
from flask_cors import CORS
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError
from flask import jsonify
from werkzeug.middleware.proxy_fix import ProxyFix

# 本地模块
from app.routes.auth import auth_bp
from app.routes.main import main_bp, prepare_runtime_app_config
from app.routes.api import api_bp
from app.config import Config
from app.utils.auth import PatchedRedisSessionInterface

# 调试器
from app.debugger import session_debugger

csrf = CSRFProtect()


def create_app():
    """Create the Flask application with shared middleware and extensions."""
    template_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'templates')
    app = Flask(__name__, template_folder=template_dir)

    app.config.from_object(Config)
    prepare_runtime_app_config(app)
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=app.config['PROXY_FIX_X_FOR'],
        x_proto=app.config['PROXY_FIX_X_PROTO'],
        x_host=app.config['PROXY_FIX_X_HOST'],
    )

    CORS(app, supports_credentials=True, origins=app.config['CORS_ALLOWED_ORIGINS'])
    
    app.config.update(
        SESSION_REDIS=redis.from_url(app.config['SESSION_REDIS']),
        SESSION_PERMANENT=True,
    )
    
    Session(app)

    app.session_interface = PatchedRedisSessionInterface(
        app.config['SESSION_REDIS'],
        app.config['SESSION_KEY_PREFIX'],
        app.config['SESSION_USE_SIGNER'],
        app.config['SESSION_PERMANENT'],
    )

    csrf.init_app(app)

    @app.after_request
    def apply_security_headers(response):
        """Apply baseline response hardening shared by API and HTML routes."""
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
        response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        if app.config.get('SESSION_COOKIE_SECURE', False):
            response.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
        return response
    
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp)

    @app.errorhandler(CSRFError)
    def handle_csrf_error(e):
        return jsonify({"data": None, "error": e.description or "CSRF token missing or incorrect."}), 400

    session_debugger.init_app(app)
    
    return app
