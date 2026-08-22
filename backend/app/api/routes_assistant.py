from __future__ import annotations

from fastapi import APIRouter, Depends

from app.assistant.service import chat_with_assistant
from app.core.auth import Principal, require_api_key
from app.schemas.common import AssistantChatRequest, AssistantChatResponse


router = APIRouter(prefix="/api/assistant", dependencies=[Depends(require_api_key)])


@router.post("/chat", response_model=AssistantChatResponse)
def assistant_chat(
    payload: AssistantChatRequest,
    _principal: Principal = Depends(require_api_key),
) -> AssistantChatResponse:
    return chat_with_assistant(payload)
