"""Theme management for slide presentations."""

def hex_to_rgb_float(hex_color):
    """Convert hex color to RGB floats (0-1 range)."""
    hex_color = hex_color.lstrip('#')
    rgb = tuple(int(hex_color[i:i+2], 16) / 255.0 for i in (0, 2, 4))
    return {
        'red': rgb[0],
        'green': rgb[1],
        'blue': rgb[2],
        'alpha': 1.0
    }

PRESENTATION_THEMES = {
    'corporate': {
        'name': 'Corporate',
        'description': 'Clean and professional',
        'colors': {
            'background': '#FFFFFF',
            'title_text': '#003366',
            'body_text': '#000000',
            'shape_fill': '#E6EEF4'
        }
    },
    'elegant': {
        'name': 'Elegant',
        'description': 'Soft, classy tones',
        'colors': {
            'background': '#FDFDFD',
            'title_text': '#5C5470',
            'body_text': '#333333',
            'shape_fill': '#EAEAEA'
        }
    },
    'vibrant': {
        'name': 'Vibrant',
        'description': 'Energetic and colorful',
        'colors': {
            'background': '#FFFBEC',
            'title_text': '#FF6F00',
            'body_text': '#212121',
            'shape_fill': '#FFD180'
        }
    },
    'minimal': {
        'name': 'Minimal',
        'description': 'Modern and clean',
        'colors': {
            'background': '#FAFAFA',
            'title_text': '#212121',
            'body_text': '#424242',
            'shape_fill': '#BDBDBD'
        }
    },
    'dark': {
        'name': 'Dark Mode',
        'description': 'High contrast',
        'colors': {
            'background': '#1E1E1E',
            'title_text': '#F5F5F5',
            'body_text': '#E0E0E0',
            'shape_fill': '#333333'
        }
    }
}

def get_theme(theme_id):
    """Get a theme by its ID."""
    theme = PRESENTATION_THEMES.get(theme_id)
    if not theme:
        raise ValueError(f"Theme '{theme_id}' not found")
    
    # Convert hex colors to RGB floats
    rgb_colors = {}
    for key, hex_color in theme['colors'].items():
        rgb_colors[key] = hex_to_rgb_float(hex_color)
    
    theme['rgb_colors'] = rgb_colors
    return theme

def get_theme_choices():
    """Get list of available themes for dropdown."""
    return [
        {
            'id': theme_id,
            'name': theme['name'],
            'description': theme['description']
        }
        for theme_id, theme in PRESENTATION_THEMES.items()
    ]
