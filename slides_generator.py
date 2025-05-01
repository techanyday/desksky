from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
import openai
import os
from dotenv import load_dotenv

load_dotenv()
openai.api_key = os.getenv('OPENAI_API_KEY')

class SlidesGenerator:
    def __init__(self, credentials):
        self.service = build('slides', 'v1', credentials=credentials)
        self.drive_service = build('drive', 'v3', credentials=credentials)
        self.theme_colors = {
            'primary': {'red': 0.2, 'green': 0.2, 'blue': 0.6},
            'secondary': {'red': 0.8, 'green': 0.8, 'blue': 0.9},
            'text': {'red': 0.1, 'green': 0.1, 'blue': 0.1}
        }

    def generate_content(self, title, num_slides):
        prompt = f"Generate a presentation outline for '{title}' with {num_slides-2} content slides. Each slide should have 3-5 bullet points."
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}]
        )
        return self._parse_gpt_response(response.choices[0].message['content'])

    def create_presentation(self, title, num_slides):
        presentation = self.service.presentations().create(
            body={'title': title}
        ).execute()
        presentation_id = presentation['presentationId']
        
        # Generate content using GPT
        content = self.generate_content(title, num_slides)
        
        # Create slides
        requests = []
        
        # Intro slide
        requests.append(self._create_title_slide(title))
        
        # Content slides
        for i, slide_content in enumerate(content):
            if i % 2 == 0:
                requests.append(self._create_one_column_slide(slide_content))
            else:
                requests.append(self._create_two_column_slide(slide_content))
        
        # Outro slide
        requests.append(self._create_outro_slide())
        
        # Execute the requests
        self.service.presentations().batchUpdate(
            presentationId=presentation_id,
            body={'requests': requests}
        ).execute()
        
        return presentation_id

    def _create_title_slide(self, title):
        return {
            'createSlide': {
                'slideLayoutReference': {'predefinedLayout': 'TITLE'},
                'placeholderIdMappings': [{
                    'layoutPlaceholder': {'type': 'TITLE'},
                    'objectId': 'title'
                }],
                'elements': [{
                    'objectId': 'title',
                    'shape': {
                        'shapeType': 'TEXT_BOX',
                        'text': {'text': title}
                    }
                }]
            }
        }

    def _create_one_column_slide(self, content):
        return {
            'createSlide': {
                'slideLayoutReference': {'predefinedLayout': 'TITLE_AND_BODY'},
                'placeholderIdMappings': [
                    {
                        'layoutPlaceholder': {'type': 'TITLE'},
                        'objectId': f'title_{content["title"]}'
                    },
                    {
                        'layoutPlaceholder': {'type': 'BODY'},
                        'objectId': f'body_{content["title"]}'
                    }
                ]
            }
        }

    def _create_two_column_slide(self, content):
        return {
            'createSlide': {
                'slideLayoutReference': {'predefinedLayout': 'TWO_COLUMNS'},
                'placeholderIdMappings': [
                    {
                        'layoutPlaceholder': {'type': 'TITLE'},
                        'objectId': f'title_{content["title"]}'
                    },
                    {
                        'layoutPlaceholder': {'type': 'BODY_LEFT'},
                        'objectId': f'body_left_{content["title"]}'
                    },
                    {
                        'layoutPlaceholder': {'type': 'BODY_RIGHT'},
                        'objectId': f'body_right_{content["title"]}'
                    }
                ]
            }
        }

    def _create_outro_slide(self):
        return {
            'createSlide': {
                'slideLayoutReference': {'predefinedLayout': 'SECTION_HEADER'},
                'placeholderIdMappings': [{
                    'layoutPlaceholder': {'type': 'TITLE'},
                    'objectId': 'outro_title'
                }],
                'elements': [{
                    'objectId': 'outro_title',
                    'shape': {
                        'shapeType': 'TEXT_BOX',
                        'text': {'text': 'Thank You!'}
                    }
                }]
            }
        }

    def _parse_gpt_response(self, response):
        # Implementation to parse GPT response into structured content
        # This is a placeholder - actual implementation would parse the GPT response
        # into a list of dictionaries with title and bullets
        return [{'title': 'Sample Slide', 'bullets': ['Point 1', 'Point 2', 'Point 3']}]
