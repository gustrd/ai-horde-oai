from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.horde.routing import ModelRouter
from app.schemas.openai import ModelCard, ModelList

router = APIRouter()


@router.get("/v1/models", response_model=ModelList)
async def list_models(request: Request) -> ModelList:
    model_router: ModelRouter = request.app.state.model_router
    dummy_names = model_router.get_dummy_list()
    cards = [ModelCard(id=name) for name in dummy_names]
    return ModelList(data=cards)


@router.get("/v1/models/{model_id:path}", response_model=ModelCard)
async def get_model(model_id: str, request: Request) -> ModelCard:
    model_router: ModelRouter = request.app.state.model_router
    dummy_names = model_router.get_dummy_list()
    if model_id not in dummy_names:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")
    return ModelCard(id=model_id)
