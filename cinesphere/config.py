import os
from datetime import timedelta

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "cinesphere-super-secure-production-key-2026")
    MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    DATABASE_NAME = os.getenv("DATABASE_NAME", "cinesphere")
    TMDB_API_KEY = os.getenv("TMDB_API_KEY", "8265bd1679663a7ea12ac168da84d2e8")
    TMDB_BASE_URL = "https://api.themoviedb.org/3"
    TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p"
    
    # OAuth Configurations
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "YOUR_GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "YOUR_GOOGLE_CLIENT_SECRET")
    
    # Payment Configurations
    RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "YOUR_RAZORPAY_KEY_ID")
    RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "YOUR_RAZORPAY_KEY_SECRET")
    STRIPE_PUBLIC_KEY = os.getenv("STRIPE_PUBLIC_KEY", "YOUR_STRIPE_PUBLIC_KEY")
    STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "YOUR_STRIPE_SECRET_KEY")
    
    # Session security
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    
    # Theater business limits
    MAX_STAFF_PER_THEATER = 5
    MAX_WORKERS_PER_THEATER = 20

class DevelopmentConfig(Config):
    DEBUG = True

class ProductionConfig(Config):
    DEBUG = False

config_by_name = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig
}
