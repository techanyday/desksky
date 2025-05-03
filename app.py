import os
import json
import logging
from datetime import datetime
from functools import wraps
from urllib.parse import urlencode
import re

import openai
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, abort
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_sqlalchemy import SQLAlchemy
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('app')

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key')

# Set OpenAI API key
openai.api_key = os.getenv("OPENAI_API_KEY")

# Initialize OpenAI client
client = openai  # Use Completion API directly

# Database configuration
if os.environ.get('DATABASE_URL'):
    database_url = os.environ.get('DATABASE_URL')
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
        logger.info("Using PostgreSQL database")
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///slides.db'
    logger.info("Using SQLite database")

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Login manager setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Load Google OAuth configuration
GOOGLE_CLIENT_CONFIG = {
    'web': {
        'client_id': os.environ.get('GOOGLE_CLIENT_ID'),
        'client_secret': os.environ.get('GOOGLE_CLIENT_SECRET'),
        'redirect_uris': [os.environ.get('GOOGLE_REDIRECT_URI', 'https://decksky.onrender.com/oauth2callback')],
        'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
        'token_uri': 'https://oauth2.googleapis.com/token'
    }
}

# OAuth scopes
OAUTH_SCOPES = [
    'openid',
    'https://www.googleapis.com/auth/userinfo.email',
    'https://www.googleapis.com/auth/userinfo.profile',
    'https://www.googleapis.com/auth/presentations'
]

logger.info(f"Configured redirect URI: {GOOGLE_CLIENT_CONFIG['web']['redirect_uris'][0]}")

# OAUTHLIB_INSECURE_TRANSPORT must be enabled for local development
if os.environ.get('FLASK_ENV') == 'development':
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

# Database Models
class User(UserMixin, db.Model):
    __tablename__ = 'user'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    free_credits = db.Column(db.Integer, default=3)
    subscription_status = db.Column(db.String(20), default='free')  # free, premium
    subscription_end = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    payments = db.relationship('Payment', backref='user', lazy=True, cascade='all, delete-orphan')

    def __init__(self, email):
        self.email = email
        self.free_credits = 3
        self.subscription_status = 'free'
        self.subscription_end = None

class Payment(db.Model):
    __tablename__ = 'payment'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(3), default='USD')
    status = db.Column(db.String(20), nullable=False)  # success, pending, failed
    payment_type = db.Column(db.String(20), nullable=False)  # credits, subscription
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    reference = db.Column(db.String(100), unique=True)

class Presentation(db.Model):
    __tablename__ = 'presentation'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    num_slides = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), default='pending')  # pending, completed, failed
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    google_presentation_id = db.Column(db.String(100), unique=True)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def credentials_from_session():
    """Get OAuth2 credentials from the session."""
    if 'credentials' not in session:
        return None
        
    creds_data = session['credentials']
    return Credentials(
        token=creds_data['token'],
        refresh_token=creds_data['refresh_token'],
        token_uri=creds_data['token_uri'],
        client_id=GOOGLE_CLIENT_CONFIG['web']['client_id'],
        client_secret=GOOGLE_CLIENT_CONFIG['web']['client_secret'],
        scopes=creds_data['scopes']
    )

# Ensure database is created with proper schema
def init_db():
    with app.app_context():
        # Drop all tables with proper cascading
        try:
            logger.info("Dropping all tables...")
            db.session.execute(db.text('DROP SCHEMA public CASCADE'))
            db.session.execute(db.text('CREATE SCHEMA public'))
            db.session.commit()
            logger.info("Successfully dropped and recreated schema")
        except Exception as e:
            logger.error(f"Error dropping schema: {str(e)}")
            db.session.rollback()
        
        try:
            # Create all tables
            logger.info("Creating all tables...")
            db.create_all()
            logger.info("Database initialized successfully")
        except Exception as e:
            logger.error(f"Error creating tables: {str(e)}")
            db.session.rollback()
            raise

# Initialize database on startup
init_db()

# Routes
@app.route('/')
def index():
    return render_template('index.html')

def generate_slide_content_with_gpt(title, topic, num_slides):
    """Generate slide content using GPT-3"""
    try:
        prompt = f"""Create a detailed presentation outline about {topic} with {num_slides} slides.
        Return a JSON array where each object represents a slide with:
        - "type": one of ["TITLE", "AGENDA", "BODY", "EXAMPLES", "CONCLUSION"]
        - "main_points": array of bullet points for that slide
        Format references without quotes. Example:
        {{"type": "REFERENCES", "main_points": ["References", "1. The Future of AI by Stuart Russell", "2. Life 3.0 by Max Tegmark"]}}
        """
        
        response = openai.Completion.create(
            model="gpt-3.5-turbo-instruct",
            prompt=prompt,
            max_tokens=1000,
            temperature=0.7
        )
        
        logger.info(f"OpenAI raw response: {json.dumps(response, indent=2)}")
        content = response.choices[0].text.strip()
        logger.info(f"Raw content: {content}")
        
        # Clean up the content by replacing smart quotes and ensuring proper JSON format
        content = content.replace('"', '"').replace('"', '"')
        logger.info(f"Cleaned content: {content}")
        
        slides = json.loads(content)
        return slides

    except json.JSONDecodeError as e:
        logger.error(f"JSON parsing error: {str(e)}")
        logger.error(f"Content that failed to parse: {content}")
        return None
    except Exception as e:
        logger.error(f"Error generating slide content: {str(e)}")
        return None

def transform_slide_content(slide):
    """Transform the OpenAI response into slide content with proper layouts."""
    layout_mapping = {
        'TITLE': 'TITLE',
        'AGENDA': 'SECTION_HEADER',
        'BODY': 'TITLE_AND_BODY',
        'EXAMPLES': 'TITLE_AND_BODY',
        'CONCLUSION': 'SECTION_HEADER',
        'REFERENCES': 'TITLE_AND_BODY'
    }
    
    return layout_mapping.get(slide.get('type', 'BODY'), 'TITLE_AND_BODY')

@app.route('/create-slides', methods=['GET', 'POST'])
@login_required
def create_slides():
    if request.method == 'POST':
        title = request.form.get('title')
        topic = request.form.get('topic')
        num_slides = int(request.form.get('num_slides', 5))  # Default to 5 slides
        
        try:
            # Create a new presentation
            service = build('slides', 'v1', credentials=credentials_from_session())
            presentation = service.presentations().create(
                body={'title': title}
            ).execute()
            presentation_id = presentation.get('presentationId')
            
            # Generate slide content
            slides_content = generate_slide_content_with_gpt(title, topic, num_slides)
            if not slides_content:
                return jsonify({
                    'success': False, 
                    'error': 'Failed to generate slide content'
                })
            
            # Create slides
            requests = []
            for i, slide in enumerate(slides_content):
                layout = transform_slide_content(slide)
                requests.append({
                    'createSlide': {
                        'slideLayoutReference': {
                            'predefinedLayout': layout
                        },
                        'placeholderIdMappings': []
                    }
                })
            
            # Execute the requests
            service.presentations().batchUpdate(
                presentationId=presentation_id,
                body={'requests': requests}
            ).execute()
            
            # Save to database
            try:
                new_presentation = Presentation(
                    user_id=current_user.id,
                    title=title,
                    num_slides=num_slides,
                    status='pending',
                    google_presentation_id=presentation_id
                )
                db.session.add(new_presentation)
                db.session.commit()
            except Exception as e:
                logger.error(f"Error saving presentation to database: {str(e)}")
                # Continue even if database save fails
            
            presentation_url = f"https://docs.google.com/presentation/d/{presentation_id}/edit"
            return jsonify({
                'success': True,
                'presentation_url': presentation_url
            })
            
        except Exception as e:
            logger.error(f"Error creating presentation: {str(e)}")
            return jsonify({
                'success': False,
                'error': str(e)
            })
    
    return render_template('create_slides.html')

@app.route('/presentation/<presentation_id>')
@login_required
def view_presentation(presentation_id):
    """View a specific presentation."""
    try:
        # First check our database
        presentation = Presentation.query.filter_by(google_presentation_id=presentation_id).first()
        
        if not presentation:
            flash('Presentation not found', 'error')
            return redirect(url_for('index'))
        
        # Get credentials from session
        if 'credentials' not in session:
            return redirect(url_for('login'))
        
        credentials = Credentials(**session['credentials'])
        service = build('slides', 'v1', credentials=credentials)
        
        # Get presentation details from Google Slides
        presentation_details = service.presentations().get(
            presentationId=presentation_id
        ).execute()
        
        return render_template(
            'view_presentation.html',
            presentation=presentation,
            google_slides_url=f"https://docs.google.com/presentation/d/{presentation_id}/edit",
            presentation_details=presentation_details
        )
        
    except Exception as e:
        logger.error(f"Error viewing presentation: {str(e)}")
        flash('Error viewing presentation', 'error')
        return redirect(url_for('index'))

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
        flow = Flow.from_client_config(
            GOOGLE_CLIENT_CONFIG,
            scopes=OAUTH_SCOPES,
            state=state
        )
        flow.redirect_uri = GOOGLE_CLIENT_CONFIG['web']['redirect_uris'][0]
        
        # Get authorization code from request
        authorization_response = request.url
        flow.fetch_token(authorization_response=authorization_response)
        
        # Get credentials and store in session
        credentials = flow.credentials
        session['credentials'] = {
            'token': credentials.token,
            'refresh_token': credentials.refresh_token,
            'token_uri': credentials.token_uri,
            'scopes': credentials.scopes
        }
        
        # Get user info
        service = build('oauth2', 'v2', credentials=credentials)
        user_info = service.userinfo().get().execute()
        email = user_info.get('email')
        
        logger.info(f"Retrieved user info for email: {email}")
        
        # Create or get user
        user = User.query.filter_by(email=email).first()
        if not user:
            logger.info(f"Creating new user for email: {email}")
            user = User(email=email)
            db.session.add(user)
            db.session.commit()
        
        login_user(user)
        logger.info(f"Successfully logged in user: {email}")
        
        return redirect(url_for('index'))
        
    except Exception as e:
        logger.error(f"Error in OAuth callback: {str(e)}")
        return f"Error: {str(e)}", 500

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

if __name__ == '__main__':
    app.run(debug=True)
