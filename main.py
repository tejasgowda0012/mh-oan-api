from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from contextlib import asynccontextmanager

load_dotenv()

# Import all routers
from app.routers import chat, transcribe, suggestions, tts, health

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan events for startup and shutdown"""
    import asyncio
    import logging

    log = logging.getLogger(__name__)
    loop = asyncio.get_running_loop()
    try:
        from app.services.memory import memory_service
        await loop.run_in_executor(None, memory_service._get_client)
    except Exception:
        log.warning("memory service warm-up failed", exc_info=True)

    print(f"🚀 {settings.app_name} starting up...")
    print(f"📍 Environment: {settings.environment}")
    print(f"🔧 Debug mode: {settings.debug}")
    print(f"🌐 CORS origins: {settings.allowed_origins}")
    yield
    print(f"🛑 {settings.app_name} shutting down...")

# Create FastAPI app with settings
app = FastAPI(
    title=settings.app_name,
    debug=settings.debug,
    description="AI-powered Voice Assistant API for Agricultural Support",
    lifespan=lifespan
)

# Add CORS middleware with enhanced settings
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=settings.allowed_credentials,
    allow_methods=settings.allowed_methods,
    allow_headers=settings.allowed_headers,
)


@app.get("/")
async def root():
    """Root endpoint with app information"""
    return {
        "app": settings.app_name,
        "environment": settings.environment,
        "debug": settings.debug,
        "api_prefix": settings.api_prefix
    }

# Include all routers with API prefix from settings
app.include_router(chat.router, prefix=settings.api_prefix)
app.include_router(transcribe.router, prefix=settings.api_prefix)
app.include_router(suggestions.router, prefix=settings.api_prefix)
app.include_router(tts.router, prefix=settings.api_prefix)
app.include_router(health.router, prefix=settings.api_prefix) 