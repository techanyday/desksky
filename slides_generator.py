from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
import openai
import os
import logging
import re
import json
from dotenv import load_dotenv

load_dotenv()
openai.api_key = os.getenv('OPENAI_API_KEY')
logger = logging.getLogger(__name__)

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
        """Generate presentation content using GPT-3.5-turbo"""
        try:
            prompt = f"""Create a presentation outline for '{title}' with {num_slides-2} content slides.
            Format the response as a JSON array of slides. Each slide should have:
            1. type: one of [TITLE, AGENDA, BODY, EXAMPLES, CONCLUSION]
            2. main_points: array of bullet points (3-5 points per slide)
            
            First slide should be TITLE type, second AGENDA, last CONCLUSION.
            Keep points concise and clear."""
            
            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[{
                    "role": "system",
                    "content": "You are a presentation expert that creates well-structured slide content."
                }, {
                    "role": "user",
                    "content": prompt
                }],
                temperature=0.7,
                max_tokens=1000
            )
            
            content = response.choices[0].message['content'].strip()
            return self._parse_gpt_response(content)
            
        except Exception as e:
            logger.error(f"Error generating content: {str(e)}")
            return None

    def _parse_gpt_response(self, response):
        """Parse GPT response into structured content"""
        try:
            # Clean up the response
            response = response.replace("'", '"')  # Replace single quotes with double quotes
            response = re.sub(r'```json\s*|\s*```', '', response)  # Remove code blocks if present
            
            # Parse JSON
            slides = json.loads(response)
            
            # Validate structure
            if not isinstance(slides, list):
                raise ValueError("Response is not a list of slides")
            
            for slide in slides:
                if not isinstance(slide, dict):
                    raise ValueError("Slide is not a dictionary")
                if 'type' not in slide or 'main_points' not in slide:
                    raise ValueError("Slide missing required fields")
                if not isinstance(slide['main_points'], list):
                    raise ValueError("main_points is not a list")
                if not slide['main_points']:
                    raise ValueError("main_points is empty")
                
                # Clean up points
                slide['main_points'] = [
                    point.strip().replace('"', "'")  # Use single quotes in content
                    for point in slide['main_points']
                    if point.strip()
                ]
            
            return slides
            
        except json.JSONDecodeError as e:
            logger.error(f"JSON parsing error: {str(e)}")
            logger.error(f"Problematic response: {response}")
            return None
        except Exception as e:
            logger.error(f"Error parsing GPT response: {str(e)}")
            return None

    def create_presentation(self, title, num_slides):
        """Create a Google Slides presentation"""
        try:
            # Create presentation
            presentation = self.service.presentations().create(
                body={'title': title}
            ).execute()
            presentation_id = presentation['presentationId']
            
            # Get the ID of the default first slide
            default_slide_id = presentation.get('slides', [{}])[0].get('objectId')
            
            # Generate content
            content = self.generate_content(title, num_slides)
            if not content:
                raise ValueError("Failed to generate slide content")
            
            # Create slides
            requests = []
            
            # Delete the default first slide if it exists
            if default_slide_id:
                requests.append({
                    'deleteObject': {
                        'objectId': default_slide_id
                    }
                })
            
            # Add each slide
            for slide in content:
                slide_type = slide['type']
                points = slide['main_points']
                
                # Skip agenda slides
                if slide_type == 'AGENDA':
                    continue
                    
                if slide_type == 'TITLE':
                    requests.append(self._create_title_slide(points[0], points[1] if len(points) > 1 else None))
                else:
                    requests.append(self._create_content_slide(points[0], points[1:]))
            
            # Execute the requests
            if requests:
                self.service.presentations().batchUpdate(
                    presentationId=presentation_id,
                    body={'requests': requests}
                ).execute()
            
            return presentation_id
            
        except Exception as e:
            logger.error(f"Error creating presentation: {str(e)}")
            return None

    def _create_title_slide(self, title, subtitle=None):
        """Create a title slide"""
        elements = [{
            'insertText': {
                'objectId': 'TITLE',
                'text': title
            }
        }]
        
        if subtitle:
            elements.append({
                'insertText': {
                    'objectId': 'SUBTITLE',
                    'text': subtitle
                }
            })
        
        return {
            'createSlide': {
                'slideLayoutReference': {
                    'predefinedLayout': 'TITLE'
                },
                'placeholderIdMappings': []
            }
        }

    def _create_agenda_slide(self, points):
        """Create an agenda slide"""
        return {
            'createSlide': {
                'slideLayoutReference': {
                    'predefinedLayout': 'SECTION_HEADER'
                },
                'placeholderIdMappings': []
            }
        }

    def _create_content_slide(self, title, points):
        """Create a content slide with title and bullet points"""
        return {
            'createSlide': {
                'slideLayoutReference': {
                    'predefinedLayout': 'TITLE_AND_BODY'
                },
                'placeholderIdMappings': []
            }
        }

    def transform_slide_to_requests(self, slide):
        """Transform slide content into Google Slides API requests"""
        try:
            requests = []
            slide_type = slide.get('type', '').upper()
            
            # Process based on slide type
            if slide_type == 'TITLE':
                requests.extend([
                    {
                        'insertText': {
                            'objectId': '{{TITLE}}',
                            'text': slide.get('title', '')
                        }
                    },
                    {
                        'insertText': {
                            'objectId': '{{SUBTITLE}}',
                            'text': slide.get('subtitle', '')
                        }
                    }
                ])
                if slide.get('presenter'):
                    requests.append({
                        'insertText': {
                            'objectId': '{{BODY}}',
                            'text': f"Presenter: {slide.get('presenter')}"
                        }
                    })
                if slide.get('date'):
                    requests.append({
                        'insertText': {
                            'objectId': '{{FOOTER}}',
                            'text': f"Date: {slide.get('date')}"
                        }
                    })
            elif slide_type in ['SECTION', 'BODY', 'CONTENT']:
                requests.extend([
                    {
                        'insertText': {
                            'objectId': '{{TITLE}}',
                            'text': slide.get('title', '')
                        }
                    }
                ])
                if slide.get('points'):
                    bullet_points = '\n• ' + '\n• '.join(slide.get('points'))
                    requests.append({
                        'insertText': {
                            'objectId': '{{BODY}}',
                            'text': bullet_points.strip()
                        }
                    })
                if slide.get('visual_guidance'):
                    requests.append({
                        'insertText': {
                            'objectId': '{{FOOTER}}',
                            'text': f"Visual Guidance: {slide.get('visual_guidance')}"
                        }
                    })
            elif slide_type == 'SUMMARY':
                requests.extend([
                    {
                        'insertText': {
                            'objectId': '{{TITLE}}',
                            'text': slide.get('title', 'Key Takeaways')
                        }
                    }
                ])
                if slide.get('points'):
                    bullet_points = '\n• ' + '\n• '.join(slide.get('points'))
                    requests.append({
                        'insertText': {
                            'objectId': '{{BODY}}',
                            'text': bullet_points.strip()
                        }
                    })
            elif slide_type == 'CLOSING':
                requests.extend([
                    {
                        'insertText': {
                            'objectId': '{{TITLE}}',
                            'text': slide.get('title', 'Thank You')
                        }
                    },
                    {
                        'insertText': {
                            'objectId': '{{SUBTITLE}}',
                            'text': slide.get('subtitle', '')
                        }
                    }
                ])
                if slide.get('contact'):
                    requests.append({
                        'insertText': {
                            'objectId': '{{FOOTER}}',
                            'text': f"Contact: {slide.get('contact')}"
                        }
                    })

            return requests

        except Exception as e:
            print(f"Error transforming slide: {str(e)}")
            return None
