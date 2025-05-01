from flask import Flask, render_template, request, jsonify, session, redirect, url_for, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, current_user, logout_user
import os
from datetime import datetime
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import json
import logging

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY')
if not app.secret_key:
    app.secret_key = os.urandom(24)  # Fallback for development
    logger.warning("No SECRET_KEY set, using random key")

# Database configuration
database_url = os.getenv('DATABASE_URL')
if database_url and database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)
    logger.info("Using PostgreSQL database")
else:
    logger.info("Using SQLite database")

app.config['SQLALCHEMY_DATABASE_URI'] = database_url or 'sqlite:///slides.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Google OAuth2 Configuration
client_id = os.getenv("GOOGLE_CLIENT_ID")
client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", "https://decksky.onrender.com/oauth2callback")

if not client_id or not client_secret:
    logger.error("Google OAuth credentials not configured properly")

GOOGLE_CLIENT_CONFIG = {
    "web": {
        "client_id": client_id,
        "client_secret": client_secret,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [redirect_uri]
    }
}

logger.info(f"Configured redirect URI: {redirect_uri}")

# Define OAuth scopes
OAUTH_SCOPES = [
    'openid',
    'https://www.googleapis.com/auth/userinfo.email',
    'https://www.googleapis.com/auth/userinfo.profile',
    'https://www.googleapis.com/auth/presentations'
]

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Database Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    free_credits = db.Column(db.Integer, default=3)
    subscription_status = db.Column(db.String(20), default='free')
    subscription_end = db.Column(db.DateTime)

class Presentation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    slides_count = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    presentation_id = db.Column(db.String(200))

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/create-slides', methods=['GET', 'POST'])
@login_required
def create_slides():
    if request.method == 'POST':
        title = request.form.get('title')
        num_slides = int(request.form.get('num_slides', 5))
        
        # Check user credits/subscription
        if not check_user_credits(current_user, num_slides):
            return jsonify({'error': 'Insufficient credits'})
            
        # Generate slides logic will be implemented in slides_generator.py
        return jsonify({'status': 'processing'})
        
    return render_template('create_slides.html')

@app.route('/pricing')
def pricing():
    return render_template('pricing.html')

@app.route('/login')
def login():
    if current_user.is_authenticated:
        logger.info("Already authenticated user attempting to login")
        return redirect(url_for('index'))
    
    if not GOOGLE_CLIENT_CONFIG["web"]["client_id"] or not GOOGLE_CLIENT_CONFIG["web"]["client_secret"]:
        logger.error("Missing OAuth credentials")
        return render_template('error.html', 
                            error_code=500, 
                            error_message="OAuth not configured. Please contact support."), 500
    
    try:
        flow = Flow.from_client_config(
            GOOGLE_CLIENT_CONFIG,
            scopes=OAUTH_SCOPES
        )
        
        flow.redirect_uri = GOOGLE_CLIENT_CONFIG["web"]["redirect_uris"][0]
        authorization_url, state = flow.authorization_url(access_type='offline', include_granted_scopes='true')
        
        session['state'] = state
        logger.info("Starting OAuth flow, redirecting to Google")
        return redirect(authorization_url)
    except Exception as e:
        logger.error(f"Error in login route: {str(e)}", exc_info=True)
        return render_template('error.html', 
                            error_code=500, 
                            error_message="Authentication error. Please try again."), 500

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/oauth2callback')
def oauth2callback():
    try:
        state = session.get('state')
        if not state:
            logger.error("No state in session")
            return redirect(url_for('login'))
        
        logger.info("Received OAuth callback")
        flow = Flow.from_client_config(
            GOOGLE_CLIENT_CONFIG,
            scopes=OAUTH_SCOPES,
            state=state
        )
        flow.redirect_uri = GOOGLE_CLIENT_CONFIG["web"]["redirect_uris"][0]
        
        authorization_response = request.url
        logger.info(f"Authorization response URL: {authorization_response}")
        
        flow.fetch_token(authorization_response=authorization_response)
        
        credentials = flow.credentials
        session['credentials'] = {
            'token': credentials.token,
            'refresh_token': credentials.refresh_token,
            'token_uri': credentials.token_uri,
            'client_id': credentials.client_id,
            'client_secret': credentials.client_secret,
            'scopes': credentials.scopes
        }
        
        # Get user info from Google
        oauth2_client = build('oauth2', 'v2', credentials=credentials)
        user_info = oauth2_client.userinfo().get().execute()
        logger.info(f"Retrieved user info for email: {user_info.get('email')}")
        
        # Find or create user
        user = User.query.filter_by(email=user_info['email']).first()
        if not user:
            logger.info(f"Creating new user for email: {user_info.get('email')}")
            user = User(email=user_info['email'])
            db.session.add(user)
            db.session.commit()
        
        login_user(user)
        logger.info(f"Successfully logged in user: {user.email}")
        return redirect(url_for('index'))
    except Exception as e:
        logger.error(f"Error in oauth2callback: {str(e)}", exc_info=True)
        db.session.rollback()
        return render_template('error.html', 
                            error_code=500, 
                            error_message="Authentication failed. Please try again."), 500

def check_user_credits(user, num_slides):
    if user.subscription_status == 'premium':
        return True
    elif user.subscription_status == 'free':
        return user.free_credits > 0 and num_slides <= 5
    return False

# Error handlers
@app.errorhandler(404)
def not_found_error(error):
    return render_template('error.html', error_code=404, error_message="Page not found"), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return render_template('error.html', error_code=500, error_message="Internal server error"), 500

# Ensure database is created
with app.app_context():
    try:
        db.create_all()
        app.logger.info("Database tables created successfully")
    except Exception as e:
        app.logger.error(f"Error creating database tables: {str(e)}")

if __name__ == '__main__':
    app.run(debug=True)
