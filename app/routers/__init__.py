# Import all routers to make them available when importing from app.routers
# This allows main.py to do: from app.routers import chat, transcribe, suggestions, tts, chat_bhili
from . import chat
from . import agui
from . import ag_ui
from . import transcribe
# from . import suggestions
from . import tts
from . import health
from . import upload