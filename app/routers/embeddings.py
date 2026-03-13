from fastapi import APIRouter, HTTPException

router = APIRouter()

@router.post("/v1/embeddings")
async def embeddings():
    """Stub for embeddings endpoint, which is not supported by AI Horde."""
    raise HTTPException(
        status_code=400,
        detail="Embeddings are not supported by the AI Horde OpenAI proxy."
    )
