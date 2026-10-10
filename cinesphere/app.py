import os
from datetime import UTC, datetime
from bson import ObjectId
from flask import Flask, render_template, session, request
from cinesphere.config import config_by_name
from cinesphere.models.database import init_db, get_db

def create_app(config_name=None):
    if not config_name:
        config_name = os.getenv("FLASK_ENV", "development")

    app = Flask(__name__,
                template_folder="templates",
                static_folder="static")
    
    app.config.from_object(config_by_name.get(config_name, config_by_name["default"]))

    # Initialize MongoDB
    init_db(app)

    # Initialize OAuth
    from cinesphere.oauth import oauth
    oauth.init_app(app)
    oauth.register(
        name='google',
        client_id=app.config.get('GOOGLE_CLIENT_ID'),
        client_secret=app.config.get('GOOGLE_CLIENT_SECRET'),
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={
            'scope': 'openid email profile'
        }
    )

    # Register blueprints
    from cinesphere.routes.public_routes import public_bp
    from cinesphere.routes.auth_routes import auth_bp
    from cinesphere.routes.movie_routes import movie_bp
    from cinesphere.routes.booking_routes import booking_bp
    from cinesphere.routes.customer_routes import customer_bp
    from cinesphere.routes.staff_routes import staff_bp
    from cinesphere.routes.worker_routes import worker_bp
    from cinesphere.routes.admin_routes import admin_bp
    from cinesphere.routes.api_routes import api_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(movie_bp)
    app.register_blueprint(booking_bp)
    app.register_blueprint(customer_bp)
    app.register_blueprint(staff_bp)
    app.register_blueprint(worker_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(api_bp)

    @app.before_request
    def update_active_session_seen_time():
        """Keep a lightweight activity timestamp for authenticated browser sessions."""
        record_id = session.get("session_record_id")
        if not record_id or request.endpoint == "static":
            return
        try:
            get_db().user_sessions.update_one(
                {"_id": ObjectId(record_id), "status": "active"},
                {"$set": {"last_seen_at": datetime.now(UTC).replace(tzinfo=None)}},
            )
        except Exception:
            pass

    # Error handlers
    @app.errorhandler(403)
    def forbidden(e):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def page_not_found(e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def internal_server_error(e):
        return render_template("errors/500.html"), 500

    # Disable caching for development
    @app.after_request
    def add_header(r):
        r.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        r.headers["Pragma"] = "no-cache"
        r.headers["Expires"] = "0"
        return r

    return app
