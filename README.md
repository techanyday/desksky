# DeckSky - AI-Powered Google Slides Generator

DeckSky is a web application that automatically generates professional Google Slides presentations using AI. It integrates with Google Slides API for presentation creation and Paystack for payment processing.

## Features

- Generate 3-10 slide presentations automatically
- Google OAuth 2.0 authentication
- AI-powered content generation using GPT-3.5
- Paystack integration for billing
- Free tier with 3 presentations
- Pay-per-slide option ($0.20/slide)
- Monthly subscription ($2.99/month)

## Tech Stack

- Backend: Python (Flask)
- Frontend: HTML + Tailwind CSS
- AI: OpenAI GPT-3.5
- Authentication: Google OAuth 2.0
- Payment: Paystack
- Database: SQLite

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Set up environment variables in `.env`:
```
OPENAI_API_KEY=your_openai_key
PAYSTACK_SECRET_KEY=your_paystack_key
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
```

3. Initialize the database:
```bash
flask db init
flask db migrate
flask db upgrade
```

4. Run the application:
```bash
python app.py
```

## Project Structure

```
decksky_google_slides/
├── app.py              # Main Flask application
├── slides_generator.py # Google Slides API integration
├── billing.py         # Paystack billing integration
├── requirements.txt   # Python dependencies
├── templates/         # HTML templates
│   ├── base.html
│   ├── index.html
│   ├── create_slides.html
│   └── pricing.html
└── static/           # Static assets
```

## License

MIT License
