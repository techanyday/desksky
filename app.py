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

def generate_slide_content_with_gpt(topic, slide_type):
    try:
        # Use Completion API
        response = client.Completion.create(
            engine="gpt-3.5-turbo-instruct",  # Use instruct model for completion
            prompt=f"""You are a professional presentation content creator.
Create content for a {slide_type} slide about {topic}.
Return a JSON object with this exact structure:
{{
    "title": "Your slide title here",
    "content": ["Point 1", "Point 2", "Point 3"]
}}""",
            max_tokens=500,
            temperature=0.7,
            n=1
        )
        # Log the raw response for debugging
        logger.info(f"OpenAI raw response: {response}")
        
        # Extract content from completion
        content_str = response['choices'][0]['text'].strip()
        logger.info(f"Extracted content: {content_str}")
        
        return json.loads(content_str)
    except Exception as e:
        logger.error(f"Error generating slide content: {str(e)}")
        logger.error(f"Full error details: {e.__class__.__name__}: {str(e)}")
        raise

def clean_json_string(s):
    """Clean a JSON string to ensure it's valid."""
    # Remove any whitespace before/after
    s = s.strip()
    
    # Remove any trailing commas in arrays
    s = re.sub(r',(\s*[\]}])', r'\1', s)
    
    # Ensure the string starts with [ and ends with ]
    if not s.startswith('['):
        s = '[' + s
    if not s.endswith(']'):
        s = s + ']'
    
    return s

def generate_slides_content(title, topic, num_slides):
    """Generate a complete, professional slide deck with GPT-3.5 Turbo."""
    try:
        # Create a more specific prompt for better JSON structure
        prompt = f"""Create a presentation outline about '{topic}' with {num_slides} slides.
        Return a valid JSON array where each slide has:
        - 'type': One of 'TITLE', 'AGENDA', 'BODY', 'EXAMPLES', 'CONCLUSION', 'REFERENCES'
        - 'main_points': Array of strings, first is title, rest are bullet points
        
        Return ONLY the JSON array, no other text. Format:
        [
            {{"type": "TITLE", "main_points": ["Main Title", "Subtitle"]}},
            {{"type": "BODY", "main_points": ["Section Title", "Point 1", "Point 2"]}}
        ]"""

        response = openai.Completion.create(
            model="gpt-3.5-turbo-instruct",
            prompt=prompt,
            max_tokens=1000,
            temperature=0.7
        )
        
        # Log the raw response for debugging
        logger.info(f"OpenAI raw response: {response}")
        
        # Extract and clean the content
        content_str = response.choices[0].text.strip()
        logger.info(f"Raw content: {content_str}")
        
        # Clean the JSON string
        content_str = clean_json_string(content_str)
        logger.info(f"Cleaned content: {content_str}")
        
        try:
            # Parse JSON content
            slides_content = json.loads(content_str)
            
            # Ensure it's a list
            if isinstance(slides_content, dict):
                slides_content = [slides_content]
            elif not isinstance(slides_content, list):
                raise ValueError("Content must be a list of slides")
            
            # Validate and fix each slide
            valid_slides = []
            for slide in slides_content:
                if not isinstance(slide, dict):
                    continue
                    
                # Ensure required fields exist
                if 'type' not in slide:
                    slide['type'] = 'BODY'
                if 'main_points' not in slide:
                    slide['main_points'] = ['Untitled Slide']
                
                # Ensure main_points is a list
                if not isinstance(slide['main_points'], list):
                    slide['main_points'] = [str(slide['main_points'])]
                
                # Ensure at least one main point
                if not slide['main_points']:
                    slide['main_points'] = ['Untitled Slide']
                
                valid_slides.append(slide)
            
            if not valid_slides:
                raise ValueError("No valid slides found in content")
            
            # Transform content
            return transform_slide_content(valid_slides)
            
        except json.JSONDecodeError as e:
            logger.error(f"JSON parsing error: {str(e)}")
            logger.error(f"Content that failed to parse: {content_str}")
            # Create a basic slide deck as fallback
            fallback_slides = [
                {"type": "TITLE", "main_points": [topic, "Generated Presentation"]},
                {"type": "BODY", "main_points": ["Error Creating Slides", 
                    "There was an error generating the slide content.",
                    "Please try again with different parameters."]}
            ]
            return transform_slide_content(fallback_slides)
        
    except Exception as e:
        logger.error(f"Error generating slides content: {str(e)}")
        logger.error(f"Full error details: {e.__class__.__name__}: {str(e)}")
        raise

def transform_slide_content(content):
    """Transform the OpenAI response into slide content with proper layouts."""
    # Valid Google Slides predefined layouts
    # Reference: https://developers.google.com/slides/api/reference/rest/v1/presentations.pages#Layout
    slide_type_to_layout = {
        'TITLE': 'TITLE',  # Changed from TITLE_SLIDE
        'AGENDA': 'SECTION_HEADER',
        'BODY': 'ONE_COLUMN_TEXT',
        'EXAMPLES': 'MAIN_POINT',
        'CONCLUSION': 'SECTION_HEADER',
        'REFERENCES': 'CAPTION',
        'SECTION': 'SECTION_HEADER',
        'BLANK': 'BLANK',
        'MAIN_POINT': 'MAIN_POINT',
        'SLIDE': 'ONE_COLUMN_TEXT'  # Default for generic slides
    }
    
    slides = []
    for item in content:
        try:
            # Get slide type, defaulting to 'SLIDE' if not present
            slide_type = item.get('type', 'SLIDE')
            
            # Get layout, defaulting to 'ONE_COLUMN_TEXT' if type not found
            layout = slide_type_to_layout.get(slide_type, 'ONE_COLUMN_TEXT')
            
            slide = {
                'layout': layout,
                'title': item['main_points'][0] if item.get('main_points') else '',
                'elements': []
            }
            
            # For title slides, add subtitle if available
            if layout == 'TITLE' and item.get('main_points', []) and len(item['main_points']) > 1:
                slide['subtitle'] = item['main_points'][1]
            
            # For other slides, add main points as bullet points
            elif item.get('main_points', []) and len(item['main_points']) > 1:
                slide['elements'] = [{'text': point} for point in item['main_points'][1:]]
            
            slides.append(slide)
            
        except Exception as e:
            logger.error(f"Error transforming slide content: {str(e)}")
            logger.error(f"Problematic item: {item}")
            # Create a simple error slide instead of failing
            slides.append({
                'layout': 'ONE_COLUMN_TEXT',
                'title': 'Error Creating Slide',
                'elements': [{'text': 'There was an error generating this slide content.'}]
            })
    
    return slides

def create_slide(service, presentation_id, slide_content, index):
    """Create a slide and add content."""
    try:
        # First create the slide
        create_response = service.presentations().batchUpdate(
            presentationId=presentation_id,
            body={
                'requests': [{
                    'createSlide': {
                        'objectId': f'slide_{index}',
                        'insertionIndex': index,
                        'slideLayoutReference': {
                            'predefinedLayout': slide_content.get('layout', 'ONE_COLUMN_TEXT')
                        }
                    }
                }]
            }
        ).execute()

        # Get the created slide from the response
        slide = create_response.get('replies', [{}])[0].get('createSlide', {})
        slide_id = slide.get('objectId')

        if not slide_id:
            raise ValueError("Failed to get slide ID from create response")

        # Get the layout-specific placeholder IDs
        layout_placeholders = service.presentations().pages().get(
            presentationId=presentation_id,
            pageObjectId=slide_id
        ).execute().get('pageElements', [])

        # Map placeholder types to their IDs
        placeholder_ids = {}
        for element in layout_placeholders:
            if 'shape' in element and 'placeholder' in element['shape']:
                placeholder_type = element['shape']['placeholder'].get('type')
                if placeholder_type:
                    placeholder_ids[placeholder_type] = element['objectId']

        # Prepare text insertion requests
        requests = []

        # Add title if we have a TITLE placeholder
        if 'title' in slide_content and 'TITLE' in placeholder_ids:
            requests.append({
                'insertText': {
                    'objectId': placeholder_ids['TITLE'],
                    'text': slide_content['title']
                }
            })

        # Add subtitle if we have a SUBTITLE placeholder
        if 'subtitle' in slide_content and 'SUBTITLE' in placeholder_ids:
            requests.append({
                'insertText': {
                    'objectId': placeholder_ids['SUBTITLE'],
                    'text': slide_content['subtitle']
                }
            })

        # Add body content if we have a BODY placeholder
        if 'elements' in slide_content and slide_content['elements'] and 'BODY' in placeholder_ids:
            body_text = '\n• ' + '\n• '.join(element['text'] for element in slide_content['elements'])
            requests.append({
                'insertText': {
                    'objectId': placeholder_ids['BODY'],
                    'text': body_text.strip()
                }
            })

        # Execute the text insertion requests if any
        if requests:
            service.presentations().batchUpdate(
                presentationId=presentation_id,
                body={'requests': requests}
            ).execute()

        return slide_id

    except Exception as e:
        logger.error(f"Error creating slide: {str(e)}")
        logger.error(f"Slide content: {slide_content}")
        raise

@app.route('/create-slides', methods=['GET', 'POST'])
@login_required
def create_slides():
    if request.method == 'POST':
        try:
            topic = request.form.get('topic')
            num_slides = int(request.form.get('num_slides', 6))
            
            # Get credentials from session
            if 'credentials' not in session:
                return redirect(url_for('login'))
            
            credentials = Credentials(**session['credentials'])
            service = build('slides', 'v1', credentials=credentials)
            
            # Create a new presentation
            presentation = service.presentations().create(
                body={'title': f'Presentation about {topic}'}
            ).execute()
            presentation_id = presentation.get('presentationId')
            
            # Generate slides content
            slides_content = generate_slides_content(presentation['title'], topic, num_slides)
            
            # Create each slide
            for i, slide_content in enumerate(slides_content):
                create_slide(service, presentation_id, slide_content, i)
            
            # Get the presentation URL
            presentation_url = f"https://docs.google.com/presentation/d/{presentation_id}/edit"
            
            return jsonify({
                'success': True,
                'presentation_url': presentation_url
            })
            
        except Exception as e:
            logger.error(f"Error creating slides: {str(e)}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    return render_template('create_slides.html')

@app.route('/presentation/<int:presentation_id>')
@login_required
def view_presentation(presentation_id):
    presentation = Presentation.query.get_or_404(presentation_id)
    
    # Ensure user owns this presentation
    if presentation.user_id != current_user.id:
        abort(403)
        
    return render_template(
        'presentation.html',
        presentation=presentation,
        google_presentation_url=f"https://docs.google.com/presentation/d/{presentation.google_presentation_id}/edit"
    )

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
