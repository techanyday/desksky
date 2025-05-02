import os
import json
import logging
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, abort
from flask_login import LoginManager, UserMixin, login_user, login_required, current_user, logout_user
from flask_sqlalchemy import SQLAlchemy
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('app')

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key')

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

def generate_content_for_topic(topic):
    """Generate meaningful content for any topic."""
    # Extract key phrases from topic
    topic_words = topic.lower().split()
    
    # Define content structure based on topic type
    content = {
        'overview': [
            f"Understanding {topic} in today's context",
            f"Impact and significance of {topic}",
            f"Key stakeholders and beneficiaries"
        ],
        'key_concepts': [
            f"Core principles and fundamentals of {topic}",
            f"Essential components and frameworks",
            f"Building blocks and key elements"
        ],
        'current_trends': [
            f"Latest developments in {topic}",
            f"Emerging patterns and innovations",
            f"Industry leaders and pioneers"
        ],
        'applications': [
            f"Real-world applications of {topic}",
            f"Case studies and success stories",
            f"Implementation strategies"
        ],
        'challenges': [
            f"Common challenges in {topic}",
            f"Barriers to adoption and growth",
            f"Risk factors and considerations"
        ],
        'solutions': [
            f"Best practices for {topic}",
            f"Proven solutions and approaches",
            f"Implementation guidelines"
        ],
        'future': [
            f"Future outlook for {topic}",
            f"Upcoming trends and predictions",
            f"Opportunities for growth"
        ],
        'recommendations': [
            f"Strategic recommendations for {topic}",
            f"Action items and next steps",
            f"Success metrics and KPIs"
        ]
    }
    return content

def generate_slides_content(title, topic, num_slides):
    """Generate detailed slide content using the topic and desired number of slides."""
    content = generate_content_for_topic(topic)
    
    # Always include title and conclusion slides
    slides = [
        {
            'title': title,
            'subtitle': f'A Comprehensive Overview of {topic}\nPresented by DeckSky'
        }
    ]
    
    # Calculate how many content slides we need (excluding title and conclusion)
    content_slides_needed = num_slides - 2
    
    # Add introduction if we have room
    if content_slides_needed > 0:
        slides.append({
            'title': 'Introduction',
            'bullets': content['overview']
        })
        content_slides_needed -= 1
    
    # Add main content slides
    main_sections = [
        {'title': 'Key Concepts', 'content': content['key_concepts']},
        {'title': 'Current Landscape', 'content': content['current_trends']},
        {'title': 'Applications', 'content': content['applications']},
        {'title': 'Challenges & Solutions', 'content': [
            content['challenges'][0],
            content['solutions'][0],
            f"Overcoming barriers in {topic}"
        ]},
        {'title': 'Best Practices', 'content': content['solutions']},
        {'title': 'Implementation Strategy', 'content': [
            f"Step-by-step approach to {topic}",
            f"Key success factors for {topic}",
            f"Resource requirements and planning"
        ]},
        {'title': 'Case Studies', 'content': [
            f"Success story 1: {topic} in action",
            f"Success story 2: Lessons learned",
            f"Key takeaways and insights"
        ]},
        {'title': 'Future Outlook', 'content': content['future']}
    ]
    
    # Distribute content slides based on available space
    for i in range(content_slides_needed):
        section = main_sections[i % len(main_sections)]
        slides.append({
            'title': section['title'],
            'bullets': section['content']
        })
    
    # Always add conclusion with actionable insights
    slides.append({
        'title': 'Key Takeaways',
        'bullets': [
            f"Essential insights about {topic}",
            f"Action items and next steps",
            f"Resources for further exploration"
        ]
    })
    
    return slides

def create_slide(service, presentation_id, slide_content, index):
    """Create a single slide in the presentation with enhanced formatting."""
    requests = []
    
    # Generate unique IDs for elements
    slide_id = f'slide_{index}'
    title_id = f'title_{index}'
    body_id = f'body_{index}'
    
    # Create slide with proper layout
    requests.append({
        'createSlide': {
            'objectId': slide_id,
            'insertionIndex': index,
            'slideLayoutReference': {
                'predefinedLayout': 'TITLE_AND_BODY' if 'bullets' in slide_content else 'TITLE_ONLY'
            },
            'placeholderIdMappings': [
                {
                    'layoutPlaceholder': {
                        'type': 'TITLE',
                        'index': 0
                    },
                    'objectId': title_id
                }
            ]
        }
    })
    
    # Add body placeholder for content slides
    if 'bullets' in slide_content:
        requests[-1]['createSlide']['placeholderIdMappings'].append({
            'layoutPlaceholder': {
                'type': 'BODY',
                'index': 0
            },
            'objectId': body_id
        })
    
    # Add title with formatting
    requests.append({
        'insertText': {
            'objectId': title_id,
            'insertionIndex': 0,
            'text': slide_content['title']
        }
    })
    
    # Add subtitle or bullets with enhanced formatting
    if 'subtitle' in slide_content:
        # Create text box for subtitle
        requests.append({
            'createShape': {
                'objectId': body_id,
                'shapeType': 'TEXT_BOX',
                'elementProperties': {
                    'pageObjectId': slide_id,
                    'size': {
                        'width': {'magnitude': 6000000, 'unit': 'EMU'},
                        'height': {'magnitude': 1000000, 'unit': 'EMU'}
                    },
                    'transform': {
                        'scaleX': 1,
                        'scaleY': 1,
                        'translateX': 1000000,
                        'translateY': 2500000,
                        'unit': 'EMU'
                    }
                }
            }
        })
        requests.append({
            'insertText': {
                'objectId': body_id,
                'insertionIndex': 0,
                'text': slide_content['subtitle']
            }
        })
        # Center align subtitle
        requests.append({
            'updateParagraphStyle': {
                'objectId': body_id,
                'style': {
                    'alignment': 'CENTER'
                },
                'fields': 'alignment'
            }
        })
    elif 'bullets' in slide_content:
        # Add bullets with proper formatting
        bullet_text = '\n• ' + '\n• '.join(slide_content['bullets'])
        requests.append({
            'insertText': {
                'objectId': body_id,
                'insertionIndex': 0,
                'text': bullet_text
            }
        })
        
        # Apply bullet style and formatting
        requests.append({
            'createParagraphBullets': {
                'objectId': body_id,
                'textRange': {
                    'type': 'ALL'
                },
                'bulletPreset': 'BULLET_DISC_CIRCLE_SQUARE'
            }
        })
        
        # Add text styling
        requests.append({
            'updateTextStyle': {
                'objectId': body_id,
                'style': {
                    'fontSize': {
                        'magnitude': 18,
                        'unit': 'PT'
                    },
                    'foregroundColor': {
                        'opaqueColor': {
                            'rgbColor': {
                                'red': 0.2,
                                'green': 0.2,
                                'blue': 0.2
                            }
                        }
                    }
                },
                'fields': 'fontSize,foregroundColor'
            }
        })
    
    return requests

@app.route('/create-slides', methods=['GET', 'POST'])
@login_required
def create_slides():
    if request.method == 'GET':
        return render_template('create_slides.html')
        
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
            
        # Get credentials
        credentials = credentials_from_session()
        if not credentials:
            return jsonify({'error': 'Not authenticated with Google'}), 401

        title = data.get('title', 'Untitled Presentation')
        topic = data.get('topic', '')
        num_slides = min(max(int(data.get('num_slides', 10)), 5), 20)  # Limit between 5-20 slides

        # Create a new presentation record
        presentation = Presentation(
            user_id=current_user.id,
            title=title,
            num_slides=num_slides,
            status='pending'
        )
        db.session.add(presentation)
        db.session.commit()

        # Create presentation in Google Slides
        service = build('slides', 'v1', credentials=credentials)
        slides_presentation = service.presentations().create(body={
            'title': title
        }).execute()
        
        presentation_id = slides_presentation.get('presentationId')
        
        # Generate and add slides
        slides_content = generate_slides_content(title, topic, num_slides)
        
        # Create slides batch request
        requests = []
        for i, slide_content in enumerate(slides_content):
            requests.extend(create_slide(service, presentation_id, slide_content, i))
            
        # Execute the requests
        service.presentations().batchUpdate(
            presentationId=presentation_id,
            body={'requests': requests}
        ).execute()

        # Update our presentation record
        presentation.google_presentation_id = presentation_id
        presentation.num_slides = len(slides_content)
        presentation.status = 'completed'
        db.session.commit()

        logger.info(f"Created presentation with ID: {presentation.id}")
        return jsonify({
            'presentation_id': presentation.id,
            'google_presentation_id': presentation_id
        })

    except Exception as e:
        logger.error(f"Error creating slides: {str(e)}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

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
