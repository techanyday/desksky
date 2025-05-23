import os
import json
import logging
from datetime import datetime
from functools import wraps
from urllib.parse import urlencode
import re

import openai
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, abort
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_sqlalchemy import SQLAlchemy
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from themes import get_theme_choices
from slides_generator import SlidesGenerator

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
    if not session.get('credentials'):
        return None
    
    try:
        credentials_dict = session['credentials']
        credentials = Credentials(
            token=credentials_dict['token'],
            refresh_token=credentials_dict['refresh_token'],
            token_uri=credentials_dict['token_uri'],
            client_id=GOOGLE_CLIENT_CONFIG['web']['client_id'],
            client_secret=GOOGLE_CLIENT_CONFIG['web']['client_secret'],
            scopes=credentials_dict['scopes']
        )
        
        # Check if token needs refresh
        if not credentials.valid:
            credentials.refresh(Request())
            session['credentials'] = {
                'token': credentials.token,
                'refresh_token': credentials.refresh_token,
                'token_uri': credentials.token_uri,
                'scopes': credentials.scopes
            }
        
        return credentials
    except Exception as e:
        app.logger.error(f"Error getting credentials from session: {str(e)}")
        session.pop('credentials', None)  # Clear invalid credentials
        return None

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

def transform_slide_content(slide):
    """Transform the OpenAI response into slide content with proper layouts."""
    layout_mapping = {
        'TITLE': 'TITLE',
        'AGENDA': 'SECTION_HEADER',
        'SECTION': 'TITLE_AND_BODY',
        'SUMMARY': 'TITLE_AND_BODY',
        'CLOSING': 'SECTION_HEADER'
    }
    
    try:
        slide_type = slide.get('type', 'TITLE')
        layout = layout_mapping.get(slide_type, 'TITLE_AND_BODY')
        
        # Create the slide creation request
        create_request = {
            'createSlide': {
                'slideLayoutReference': {
                    'predefinedLayout': layout
                },
                'placeholderIdMappings': []
            }
        }
        
        # Create text insertion requests based on main points
        text_requests = []
        if slide_type == 'TITLE':
            text_requests.append({
                'insertText': {
                    'objectId': '{{TITLE}}',
                    'text': slide.get('title', '')
                }
            })
            if slide.get('subtitle'):
                text_requests.append({
                    'insertText': {
                        'objectId': '{{SUBTITLE}}',
                        'text': slide.get('subtitle', '')
                    }
                })
            if slide.get('presenter'):
                text_requests.append({
                    'insertText': {
                        'objectId': '{{BODY}}',
                        'text': f"Presenter: {slide.get('presenter', '')}"
                    }
                })
            if slide.get('date'):
                text_requests.append({
                    'insertText': {
                        'objectId': '{{FOOTER}}',
                        'text': f"Date: {slide.get('date', '')}"
                    }
                })
        elif slide_type == 'AGENDA':
            text_requests.append({
                'insertText': {
                    'objectId': '{{TITLE}}',
                    'text': slide.get('title', 'Agenda')
                }
            })
            if slide.get('points'):
                bullet_points = '\n• ' + '\n• '.join(slide.get('points', []))
                text_requests.append({
                    'insertText': {
                        'objectId': '{{BODY}}',
                        'text': bullet_points.strip()
                    }
                })
        elif slide_type == 'SECTION':
            text_requests.append({
                'insertText': {
                    'objectId': '{{TITLE}}',
                    'text': slide.get('title', '')
                }
            })
            if slide.get('points'):
                bullet_points = '\n• ' + '\n• '.join(slide.get('points', []))
                text_requests.append({
                    'insertText': {
                        'objectId': '{{BODY}}',
                        'text': bullet_points.strip()
                    }
                })
            if slide.get('visual_guidance'):
                text_requests.append({
                    'insertText': {
                        'objectId': '{{FOOTER}}',
                        'text': f"Visual Guidance: {slide.get('visual_guidance', '')}"
                    }
                })
        elif slide_type == 'SUMMARY':
            text_requests.append({
                'insertText': {
                    'objectId': '{{TITLE}}',
                    'text': slide.get('title', 'Key Takeaways')
                }
            })
            if slide.get('points'):
                bullet_points = '\n• ' + '\n• '.join(slide.get('points', []))
                text_requests.append({
                    'insertText': {
                        'objectId': '{{BODY}}',
                        'text': bullet_points.strip()
                    }
                })
        elif slide_type == 'CLOSING':
            text_requests.append({
                'insertText': {
                    'objectId': '{{TITLE}}',
                    'text': slide.get('title', 'Thank You')
                }
            })
            if slide.get('subtitle'):
                text_requests.append({
                    'insertText': {
                        'objectId': '{{SUBTITLE}}',
                        'text': slide.get('subtitle', '')
                    }
                })
            if slide.get('contact'):
                text_requests.append({
                    'insertText': {
                        'objectId': '{{FOOTER}}',
                        'text': f"Contact: {slide.get('contact', '')}"
                    }
                })
        
        return {
            'layout': layout,
            'create_request': create_request,
            'text_requests': text_requests
        }
        
    except Exception as e:
        app.logger.error(f"Error transforming slide content: {str(e)}")
        app.logger.error(f"Problematic slide content: {slide}")
        return {
            'layout': 'TITLE_AND_BODY',
            'create_request': {
                'createSlide': {
                    'slideLayoutReference': {
                        'predefinedLayout': 'TITLE_AND_BODY'
                    },
                    'placeholderIdMappings': []
                }
            },
            'text_requests': []
        }

def generate_slide_content_with_gpt(title, topic, num_slides):
    """Generate slide content using GPT-3.5."""
    try:
        # Create system prompt
        system_prompt = """You are a presentation content generator. Create a JSON array of slides.

REQUIRED FORMAT - EVERY slide MUST follow this EXACT format:
{
    "title": "Title text here",  # Required string
    "content": ["Point 1", "Point 2", "Point 3"]  # Required array of strings
}

For the FIRST slide:
{
    "title": "Main presentation title",  # Use the presentation title here
    "content": ["Subtitle or introduction point"]  # Brief introduction
}

Example of a complete response:
[
    {
        "title": "Introduction to AI",  # First slide uses main title
        "content": ["Understanding the future of technology"]
    },
    {
        "title": "What is Artificial Intelligence?",  # Subsequent slides use section titles
        "content": [
            "Definition and core concepts",
            "Types of AI systems",
            "Key applications"
        ]
    }
]

STRICT REQUIREMENTS:
1. EVERY slide object MUST have EXACTLY these two fields:
   - "title": string  (REQUIRED)
   - "content": array of strings  (REQUIRED)
2. NO OTHER FIELDS are allowed (no 'type', 'main_points', etc.)
3. First slide MUST use the presentation title
4. Last slide should be a conclusion
5. Keep text simple - no special characters
6. Return ONLY the JSON array with no other text"""

        # Create user prompt
        user_prompt = f"""Create a {num_slides}-slide presentation about '{title}'. Focus: {topic}.
Remember: 
1. First slide MUST use title: "{title}"
2. Each slide must have ONLY 'title' and 'content' fields
3. NO 'type' or 'main_points' fields allowed"""

        # Get completion from OpenAI
        completion = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.7,
            max_tokens=2000
        )

        # Parse and validate the response
        content = completion.choices[0].message.content
        app.logger.info(f"Raw GPT Response: {content}")
        
        # Clean up the response
        content = (content
            .replace("```json", "")
            .replace("```", "")
            .strip()
            .replace('"', '"')  # Replace smart quotes
            .replace('"', '"')
            .replace(''', "'")
            .replace(''', "'")
            .replace('…', '...')
            .replace('–', '-')
            .replace('—', '-')
        )
        
        app.logger.info(f"Cleaned GPT Response: {content}")
        
        # Parse JSON
        try:
            slides = json.loads(content)
        except json.JSONDecodeError as e:
            app.logger.error(f"JSON parsing error at position {e.pos}: {e.msg}")
            app.logger.error(f"JSON snippet: {content[max(0, e.pos-50):min(len(content), e.pos+50)]}")
            raise
        
        # Validate slides
        if not isinstance(slides, list):
            raise ValueError(f"GPT response is not a list: {slides}")
            
        # Process and validate each slide
        processed_slides = []
        for i, slide in enumerate(slides):
            # Convert old format if needed
            if isinstance(slide, dict):
                if 'type' in slide and 'main_points' in slide:
                    app.logger.warning(f"Converting old slide format: {slide}")
                    # For any type, use first point as title and rest as content
                    title = slide['main_points'][0] if slide['main_points'] else title  # Use presentation title for first slide
                    content = slide['main_points'][1:] if len(slide['main_points']) > 1 else []
                    slide = {
                        'title': title if i > 0 else title,  # Force first slide to use presentation title
                        'content': content
                    }
                elif 'title' not in slide or 'content' not in slide:
                    app.logger.error(f"Invalid slide format at index {i}: {slide}")
                    raise ValueError(f"Slide {i} missing required fields")

            # Validate slide structure
            if not isinstance(slide, dict):
                raise ValueError(f"Slide {i} is not a dictionary: {slide}")
            if not isinstance(slide.get('content', []), list):
                raise ValueError(f"Slide {i} content is not a list: {slide}")
                
            # Create processed slide with only required fields
            processed_slide = {
                'id': f'slide_{i+1}',
                'title': title if i == 0 else str(slide.get('title', '')).strip() or f"Slide {i+1}",  # Force first slide title
                'content': [str(point).strip() for point in slide.get('content', [])]
            }
            processed_slides.append(processed_slide)
            
        app.logger.info(f"Processed slides: {json.dumps(processed_slides, indent=2)}")
        return processed_slides

    except json.JSONDecodeError as e:
        app.logger.error(f"JSON parsing error: {str(e)}")
        app.logger.error(f"Problematic response: {content}")
        raise ValueError("Failed to generate slide content")
    except Exception as e:
        app.logger.error(f"Error generating slide content: {str(e)}")
        raise ValueError("Failed to generate slide content")

@app.route('/api/themes', methods=['GET'])
def get_themes():
    """Get available presentation themes."""
    try:
        themes = get_theme_choices()
        return jsonify({'themes': themes})
    except Exception as e:
        app.logger.error(f"Error getting themes: {str(e)}")
        return jsonify({'error': 'Failed to get themes'}), 500

@app.route('/api/presentations', methods=['POST'])
def create_presentation():
    """Create a new presentation."""
    try:
        data = request.get_json()
        title = data.get('title')
        num_slides = int(data.get('num_slides', 8))
        theme_id = data.get('theme_id', 'corporate')
        
        if not title:
            return jsonify({'error': 'Title is required'}), 400
            
        credentials = credentials_from_session()
        if not credentials:
            return jsonify({'error': 'Not authenticated'}), 401
            
        # Create slides generator with selected theme
        generator = SlidesGenerator(credentials, theme_id)
        presentation_id = generator.create_presentation(title, num_slides)
        
        if not presentation_id:
            return jsonify({'error': 'Failed to create presentation'}), 500
            
        # Get presentation URL
        presentation_url = f"https://docs.google.com/presentation/d/{presentation_id}"
        
        return jsonify({
            'presentation_id': presentation_id,
            'url': presentation_url
        })
        
    except Exception as e:
        app.logger.error(f"Error creating presentation: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/create-slides', methods=['GET', 'POST'])
@login_required
def create_slides():
    if request.method == 'POST':
        title = request.form.get('title')
        topic = request.form.get('topic')
        num_slides = int(request.form.get('num_slides', 5))
        
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
                    'error': 'Failed to generate slide content. Please try again.'
                })
            
            # Transform and create slides
            all_requests = []
            for slide in slides_content:
                transformed = transform_slide_content(slide)
                all_requests.append(transformed['create_request'])
            
            # First create all slides
            response = service.presentations().batchUpdate(
                presentationId=presentation_id,
                body={'requests': all_requests}
            ).execute()
            
            # Get the created slide IDs
            slide_ids = []
            for reply in response.get('replies', []):
                if 'createSlide' in reply:
                    slide_ids.append(reply['createSlide']['objectId'])
            
            # Now get the placeholder IDs for each slide
            text_requests = []
            for i, slide_id in enumerate(slide_ids):
                # Get slide details
                slide_details = service.presentations().pages().get(
                    presentationId=presentation_id,
                    pageObjectId=slide_id
                ).execute()
                
                # Map placeholders to their IDs
                placeholder_ids = {}
                for element in slide_details.get('pageElements', []):
                    if 'shape' in element and 'placeholder' in element['shape']:
                        placeholder_type = element['shape']['placeholder'].get('type')
                        if placeholder_type == 'TITLE':
                            placeholder_ids['{{TITLE}}'] = element['objectId']
                        elif placeholder_type == 'SUBTITLE':
                            placeholder_ids['{{SUBTITLE}}'] = element['objectId']
                        elif placeholder_type == 'BODY':
                            placeholder_ids['{{BODY}}'] = element['objectId']
                
                # Replace placeholder IDs in text requests
                transformed = transform_slide_content(slides_content[i])
                for text_request in transformed['text_requests']:
                    placeholder = text_request['insertText']['objectId']
                    if placeholder in placeholder_ids:
                        text_request['insertText']['objectId'] = placeholder_ids[placeholder]
                        text_requests.append(text_request)
            
            # Execute text insertion requests
            if text_requests:
                service.presentations().batchUpdate(
                    presentationId=presentation_id,
                    body={'requests': text_requests}
                ).execute()
            
            # Save to database
            try:
                new_presentation = Presentation(
                    user_id=current_user.id,
                    title=title,
                    num_slides=num_slides,
                    status='completed',
                    google_presentation_id=presentation_id
                )
                db.session.add(new_presentation)
                db.session.commit()
            except Exception as e:
                app.logger.error(f"Error saving presentation to database: {str(e)}")
                # Continue even if database save fails
            
            presentation_url = f"https://docs.google.com/presentation/d/{presentation_id}/edit"
            return jsonify({
                'success': True,
                'presentation_url': presentation_url
            })
            
        except Exception as e:
            app.logger.error(f"Error creating presentation: {str(e)}")
            return jsonify({
                'success': False,
                'error': str(e)
            })
    
    return render_template('create_slides.html')

@app.route('/')
def index():
    return render_template('index.html')

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
        app.logger.error(f"Error viewing presentation: {str(e)}")
        flash('Error viewing presentation', 'error')
        return redirect(url_for('index'))

@app.route('/pricing')
def pricing():
    return render_template('pricing.html')

@app.route('/login')
def login():
    if current_user.is_authenticated:
        app.logger.info("Already authenticated user attempting to login")
        return redirect(url_for('index'))
    
    if not GOOGLE_CLIENT_CONFIG["web"]["client_id"] or not GOOGLE_CLIENT_CONFIG["web"]["client_secret"]:
        app.logger.error("Missing OAuth credentials")
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
        app.logger.info("Starting OAuth flow, redirecting to Google")
        return redirect(authorization_url)
    except Exception as e:
        app.logger.error(f"Error in login route: {str(e)}", exc_info=True)
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
        # Get state from session
        state = session.get('state')
        if not state:
            raise ValueError("Missing state parameter")

        # Verify state matches
        if state != request.args.get('state'):
            raise ValueError("Invalid state parameter")

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
        
        if not email:
            raise ValueError("Could not get user email from Google")
        
        app.logger.info(f"Retrieved user info for email: {email}")
        
        # Create or get user
        user = User.query.filter_by(email=email).first()
        if not user:
            app.logger.info(f"Creating new user for email: {email}")
            user = User(email=email)
            db.session.add(user)
            db.session.commit()
        
        login_user(user)
        app.logger.info(f"Successfully logged in user: {email}")
        
        # Clear state from session
        session.pop('state', None)
        
        return redirect(url_for('index'))
        
    except Exception as e:
        app.logger.error(f"Error in OAuth callback: {str(e)}", exc_info=True)
        session.pop('state', None)  # Clear state on error
        session.pop('credentials', None)  # Clear credentials on error
        flash('Authentication failed. Please try again.', 'error')
        return redirect(url_for('login'))

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
