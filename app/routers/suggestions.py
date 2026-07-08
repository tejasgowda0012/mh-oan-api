from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from app.models.requests import SuggestionsRequest
from app.utils import get_cache
from app.auth.jwt_auth import get_current_user
from helpers.translation import BhashiniTranslator

router = APIRouter(prefix="/suggest", tags=["suggest"],)

# NOTE: No need for rate limiting here as it's invoked by the chat method only.
@router.get("/")
async def suggest(request: SuggestionsRequest = Depends(), user_info: dict = Depends(get_current_user)):
    """
    Get suggestions for a conversation session.
    If suggestions are not cached, trigger creation asynchronously.
    """
    # Bhili UI uses bhb; suggestions are generated in mr (see chat flow), then mr → bhb here.
    cache_lang = "mr" if request.target_lang == "bhb" else request.target_lang
    cache_key = f"suggestions_{request.session_id}_{cache_lang}"
    
    # Poll cache for up to 3.5 seconds to wait for background suggestions generation task to finish
    import asyncio
    suggestions = None
    for _ in range(7):  # 7 * 0.5s = 3.5s
        suggestions = await get_cache(cache_key)
        if suggestions:
            break
        await asyncio.sleep(0.5)
        
    if not suggestions:
        from helpers.utils import get_logger
        logger = get_logger(__name__)
        logger.info(f"Cache miss for suggestions, generating on-the-fly for session {request.session_id}")
        from app.tasks.suggestions import create_suggestions
        suggestions = await create_suggestions(
            session_id=request.session_id,
            target_lang=cache_lang,
            user_id=user_info.get("farmer_id")
        )
        
    suggestions = suggestions or []
    if request.target_lang == "bhb" and suggestions:
        translator = BhashiniTranslator(source_lang="mr", target_lang="bhb")
        suggestions = await translator.translate(suggestions)
    return JSONResponse(suggestions)