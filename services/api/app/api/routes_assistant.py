from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assistant.models import AssistantConversation, ResearchEvidencePackage
from app.assistant.service import MessageInput, conversation_dict, message_dict, submit_message
from app.db.session import get_session

router = APIRouter(prefix="/assistant", tags=["assistant"])


@router.post("/conversations", status_code=201)
def new_conversation(session: Session = Depends(get_session)):
    conversation = AssistantConversation()
    session.add(conversation)
    session.commit()
    return conversation_dict(session, conversation)


@router.get("/conversations")
def list_conversations(session: Session = Depends(get_session)):
    return [{"id": c.id, "title": c.title, "project_id": c.project_id} for c in session.scalars(
        select(AssistantConversation).order_by(AssistantConversation.updated_at.desc()).limit(100))]


def require_conversation(session: Session, conversation_id: str):
    conversation = session.get(AssistantConversation, conversation_id)
    if not conversation:
        raise HTTPException(404, "会话不存在")
    return conversation


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str, session: Session = Depends(get_session)):
    return conversation_dict(session, require_conversation(session, conversation_id))


@router.post("/conversations/{conversation_id}/messages", status_code=202)
def send_message(conversation_id: str, payload: MessageInput, session: Session = Depends(get_session)):
    conversation = require_conversation(session, conversation_id)
    try:
        reply = submit_message(session, conversation, payload)
    except ValueError as exc:
        session.rollback()
        raise HTTPException(409, str(exc)) from exc
    return message_dict(reply)


@router.get("/conversations/{conversation_id}/evidence/{package_id}")
def get_evidence(conversation_id: str, package_id: str, session: Session = Depends(get_session)):
    conversation = require_conversation(session, conversation_id)
    package = session.get(ResearchEvidencePackage, package_id)
    if not package or package.project_id != conversation.project_id:
        raise HTTPException(404, "证据包不存在")
    return {"id": package.id, "run_id": package.run_id, "parent_id": package.parent_id,
            "items": package.items_json, "inventory": package.inventory_json, "created_at": package.created_at}
