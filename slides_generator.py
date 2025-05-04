from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
import openai
import os
import logging
import re
import json
from dotenv import load_dotenv
from themes import get_theme
import uuid

load_dotenv()
openai.api_key = os.getenv('OPENAI_API_KEY')
logger = logging.getLogger(__name__)

class SlidesGenerator:
    def __init__(self, credentials, theme_id='corporate'):
        self.service = build('slides', 'v1', credentials=credentials)
        self.drive_service = build('drive', 'v3', credentials=credentials)
        try:
            self.theme = get_theme(theme_id)
            if not self.theme or 'rgb_colors' not in self.theme:
                logger.error(f"Invalid theme or missing rgb_colors: {theme_id}")
                # Fall back to default colors if theme loading fails
                self.theme = {
                    'rgb_colors': {
                        'background': {'red': 1.0, 'green': 1.0, 'blue': 1.0},
                        'title_text': {'red': 0.0, 'green': 0.0, 'blue': 0.0},
                        'body_text': {'red': 0.2, 'green': 0.2, 'blue': 0.2},
                        'shape_fill': {'red': 0.9, 'green': 0.9, 'blue': 0.9}
                    }
                }
        except Exception as e:
            logger.error(f"Error loading theme: {str(e)}")
            # Use default theme colors
            self.theme = {
                'rgb_colors': {
                    'background': {'red': 1.0, 'green': 1.0, 'blue': 1.0},
                    'title_text': {'red': 0.0, 'green': 0.0, 'blue': 0.0},
                    'body_text': {'red': 0.2, 'green': 0.2, 'blue': 0.2},
                    'shape_fill': {'red': 0.9, 'green': 0.9, 'blue': 0.9}
                }
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

    def _apply_theme_to_slide(self, slide_id):
        """Apply the current theme to a slide."""
        requests = []
        
        # Set background color
        requests.append({
            'updatePageProperties': {
                'objectId': slide_id,
                'pageProperties': {
                    'pageBackgroundFill': self._create_color_style(self.theme['rgb_colors']['background'])
                },
                'fields': 'pageBackgroundFill'
            }
        })
        
        # Set text styles for title and body
        requests.extend([
            {
                'updateTextStyle': {
                    'objectId': f"{slide_id}_title",
                    'style': {
                        'foregroundColor': {
                            'opaqueColor': {
                                'rgbColor': self.theme['rgb_colors']['title_text']
                            }
                        },
                        'fontSize': {
                            'magnitude': 24,
                            'unit': 'PT'
                        },
                        'bold': True
                    },
                    'fields': 'foregroundColor,fontSize,bold'
                }
            },
            {
                'updateTextStyle': {
                    'objectId': f"{slide_id}_body",
                    'style': {
                        'foregroundColor': {
                            'opaqueColor': {
                                'rgbColor': self.theme['rgb_colors']['body_text']
                            }
                        },
                        'fontSize': {
                            'magnitude': 18,
                            'unit': 'PT'
                        }
                    },
                    'fields': 'foregroundColor,fontSize'
                }
            }
        ])
        
        # Set shape fill colors
        requests.append({
            'updateShapeProperties': {
                'objectId': slide_id,
                'shapeProperties': {
                    'shapeBackgroundFill': self._create_color_style(self.theme['rgb_colors']['shape_fill'])
                },
                'fields': 'shapeBackgroundFill'
            }
        })
        
        return requests

    def _create_color_style(self, rgb_color):
        """Create a color style for Google Slides API."""
        return {
            'solidFill': {
                'color': {
                    'rgbColor': rgb_color  # rgb_color is already in correct format
                }
            }
        }

    def create_presentation(self, title, num_slides):
        """Create a new presentation."""
        try:
            # Create a new presentation
            presentation = self.service.presentations().create(
                body={'title': title}
            ).execute()
            
            presentation_id = presentation.get('presentationId')
            if not presentation_id:
                raise ValueError("Failed to get presentation ID")

            # Generate slide content
            slide_content = self.generate_content(title, num_slides)
            if not slide_content:
                raise ValueError("Failed to generate slide content")

            # Log slide content for debugging
            logger.info(f"Generated slide content: {json.dumps(slide_content, indent=2)}")

            # Transform all slides to requests
            all_requests = []
            for i, slide in enumerate(slide_content):
                # Convert old format if needed
                if isinstance(slide, dict):
                    if 'type' in slide and 'main_points' in slide:
                        logger.warning(f"Converting old slide format: {slide}")
                        # For any type, use first point as title and rest as content
                        title = slide['main_points'][0] if slide['main_points'] else "Untitled Slide"
                        content = slide['main_points'][1:] if len(slide['main_points']) > 1 else []
                        slide = {
                            'title': title,
                            'content': content
                        }

                # Validate slide structure
                if not isinstance(slide, dict):
                    raise ValueError(f"Invalid slide format at index {i}: {slide}")

                # Add slide ID if not present
                if 'id' not in slide:
                    slide['id'] = f'slide_{i+1}'

                # Ensure title and content exist
                if 'title' not in slide:
                    slide['title'] = slide.get('main_points', ["Untitled Slide"])[0] if isinstance(slide.get('main_points'), list) else "Untitled Slide"
                if 'content' not in slide and 'main_points' in slide:
                    slide['content'] = slide['main_points'][1:] if len(slide['main_points']) > 1 else []
                elif 'content' not in slide:
                    slide['content'] = []

                # Transform slide
                slide_requests, slide_id = self.transform_slide_to_requests(slide)
                all_requests.extend(slide_requests)

            # Log requests for debugging
            logger.info(f"Generated {len(all_requests)} API requests")

            # Execute the requests
            if all_requests:
                self.service.presentations().batchUpdate(
                    presentationId=presentation_id,
                    body={'requests': all_requests}
                ).execute()

            return presentation_id

        except Exception as e:
            logger.error(f"Error creating presentation: {str(e)}")
            raise ValueError("Failed to create presentation") from e

    def transform_slide_to_requests(self, slide):
        """Transform a slide into Google Slides API requests."""
        requests = []
        slide_id = str(uuid.uuid4())
        
        # Create the slide first
        requests.append({
            'createSlide': {
                'objectId': slide_id,
                'slideLayoutReference': {
                    'predefinedLayout': 'TITLE_AND_BODY'
                },
                'placeholderIdMappings': [
                    {
                        'layoutPlaceholder': {'type': 'TITLE'},
                        'objectId': f"{slide_id}_title"
                    },
                    {
                        'layoutPlaceholder': {'type': 'BODY'},
                        'objectId': f"{slide_id}_body"
                    }
                ]
            }
        })
        
        # Apply theme colors
        requests.extend(self._apply_theme_to_slide(slide_id))
        
        # Create title text box
        title_id = f"{slide_id}_title"
        requests.append({
            'insertText': {
                'objectId': title_id,
                'text': slide['title']
            }
        })
        
        # Create body text box
        body_id = f"{slide_id}_body"
        bullet_points = [f"• {str(point).strip()}" for point in slide.get('content', [])]
        body_text = "\n".join(bullet_points)
        
        requests.append({
            'insertText': {
                'objectId': body_id,
                'text': body_text
            }
        })
        
        # Apply text styles after inserting text
        requests.extend([
            {
                'updateTextStyle': {
                    'objectId': title_id,
                    'style': {
                        'foregroundColor': {
                            'opaqueColor': {
                                'rgbColor': self.theme['rgb_colors']['title_text']
                            }
                        },
                        'fontSize': {
                            'magnitude': 24,
                            'unit': 'PT'
                        },
                        'bold': True
                    },
                    'fields': 'foregroundColor,fontSize,bold'
                }
            },
            {
                'updateTextStyle': {
                    'objectId': body_id,
                    'style': {
                        'foregroundColor': {
                            'opaqueColor': {
                                'rgbColor': self.theme['rgb_colors']['body_text']
                            }
                        },
                        'fontSize': {
                            'magnitude': 18,
                            'unit': 'PT'
                        }
                    },
                    'fields': 'foregroundColor,fontSize'
                }
            }
        ])
        
        return requests, slide_id

    def _create_title_slide(self, title, subtitle=None, slide_id=None):
        """Create a title slide"""
        if not slide_id:
            slide_id = f"slide_{title[:10]}"
            
        requests = [{
            'createSlide': {
                'objectId': slide_id,
                'slideLayoutReference': {
                    'predefinedLayout': 'TITLE'
                },
                'placeholderIdMappings': [
                    {
                        'layoutPlaceholder': {'type': 'TITLE'},
                        'objectId': f"{slide_id}_title"
                    },
                    {
                        'layoutPlaceholder': {'type': 'SUBTITLE'},
                        'objectId': f"{slide_id}_body"
                    }
                ]
            }
        }]
        
        # Add title text
        requests.append({
            'insertText': {
                'objectId': f"{slide_id}_title",
                'text': title
            }
        })
        
        # Add subtitle if provided
        if subtitle:
            requests.append({
                'insertText': {
                    'objectId': f"{slide_id}_body",
                    'text': subtitle
                }
            })
        
        return requests

    def _create_content_slide(self, title, points, slide_id=None):
        """Create a content slide with title and bullet points"""
        if not slide_id:
            slide_id = f"slide_{title[:10]}"
            
        requests = [{
            'createSlide': {
                'objectId': slide_id,
                'slideLayoutReference': {
                    'predefinedLayout': 'TITLE_AND_BODY'
                },
                'placeholderIdMappings': [
                    {
                        'layoutPlaceholder': {'type': 'TITLE'},
                        'objectId': f"{slide_id}_title"
                    },
                    {
                        'layoutPlaceholder': {'type': 'BODY'},
                        'objectId': f"{slide_id}_body"
                    }
                ]
            }
        }]
        
        # Add title
        requests.append({
            'insertText': {
                'objectId': f"{slide_id}_title",
                'text': title
            }
        })
        
        # Add bullet points
        if points:
            bullet_points = '\n• ' + '\n• '.join(points)
            requests.append({
                'insertText': {
                    'objectId': f"{slide_id}_body",
                    'text': bullet_points.strip()
                }
            })
        
        return requests
