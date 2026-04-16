from pydantic import BaseModel
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.ai_client import get_client
from app.config import get_settings
from app.db import Character, GameSession, Message, SessionLocal, get_db, init_db
from app.prompts import STORY_CONTEXT, SUSPECTS_DATA


settings = get_settings()
app = FastAPI(title=settings.app_title)
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


def seed_data(db: Session) -> None:
    game = db.scalar(select(GameSession).limit(1))
    if game is None:
        game = GameSession(name="Kurim Mystery")
        db.add(game)
        db.flush()

    all_characters = db.scalars(select(Character).where(Character.game_session_id == game.id)).all()
    existing_by_suspect_id: dict[str, Character] = {}
    legacy_by_name: dict[str, Character] = {}

    for character in all_characters:
        if character.suspect_id in SUSPECTS_DATA and character.suspect_id not in existing_by_suspect_id:
            existing_by_suspect_id[character.suspect_id] = character
            continue

        if not character.suspect_id and character.name not in legacy_by_name:
            legacy_by_name[character.name] = character
            continue

        # Remove duplicate or unknown records from previous schema versions.
        if character.suspect_id not in SUSPECTS_DATA:
            db.delete(character)

    expected_names = {suspect_data["name"] for suspect_data in SUSPECTS_DATA.values()}

    for suspect_id, suspect_data in SUSPECTS_DATA.items():
        character = existing_by_suspect_id.get(suspect_id) or legacy_by_name.get(suspect_data["name"])
        if character is None:
            db.add(
                Character(
                    game_session_id=game.id,
                    suspect_id=suspect_id,
                    name=suspect_data["name"],
                    role=suspect_data["secret_role"],
                    secret_role=suspect_data["secret_role"],
                    description=suspect_data["description"],
                    system_prompt=f"{STORY_CONTEXT} {suspect_data['system_prompt']}",
                )
            )
            continue

        character.suspect_id = suspect_id
        character.name = suspect_data["name"]
        character.role = suspect_data["secret_role"]
        character.secret_role = suspect_data["secret_role"]
        character.description = suspect_data["description"]
        character.system_prompt = f"{STORY_CONTEXT} {suspect_data['system_prompt']}"

    # Remove leftover legacy records not part of current suspects.
    refreshed_characters = db.scalars(select(Character).where(Character.game_session_id == game.id)).all()
    for character in refreshed_characters:
        if character.suspect_id in SUSPECTS_DATA:
            continue
        if not character.suspect_id and character.name in expected_names:
            continue
        db.delete(character)

    db.commit()

    # Ensure suspect IDs are present even for legacy rows that survived one pass.
    refreshed_characters = db.scalars(select(Character).where(Character.game_session_id == game.id)).all()
    for character in refreshed_characters:
        if character.suspect_id:
            continue
        for suspect_id, suspect_data in SUSPECTS_DATA.items():
            if character.name == suspect_data["name"]:
                character.suspect_id = suspect_id
                break

    db.commit()


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    with SessionLocal() as db:
        seed_data(db)


def get_game_state(db: Session) -> dict:
    game = db.scalar(select(GameSession).limit(1))
    if game is None:
        raise HTTPException(status_code=500, detail="Hra nebyla inicializována.")

    characters = db.scalars(select(Character).where(Character.game_session_id == game.id).order_by(Character.id)).all()
    active_character = characters[0] if characters else None
    return {"game": game, "characters": characters, "active_character": active_character}


def reset_game_data(db: Session) -> None:
    # Hard reset after game end: clear persisted chats and game records, then rebuild suspects.
    db.execute(delete(Message))
    db.execute(delete(Character))
    db.execute(delete(GameSession))
    db.commit()
    seed_data(db)


@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    state = get_game_state(db)
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "characters": state["characters"],
            "active_suspect_id": state["active_character"].suspect_id if state["active_character"] else None,
            "app_title": settings.app_title,
        },
    )


@app.get("/api/state")
def api_state(db: Session = Depends(get_db)):
    state = get_game_state(db)
    return {
        "game": {"id": state["game"].id, "name": state["game"].name},
        "characters": [
            {
                "suspectId": character.suspect_id,
                "name": character.name,
                "description": character.description,
            }
            for character in state["characters"]
        ],
        "activeSuspectId": state["active_character"].suspect_id if state["active_character"] else None,
    }


@app.get("/api/profile/{suspect_id}")
def api_profile(suspect_id: str, db: Session = Depends(get_db)):
    character = db.scalar(select(Character).where(Character.suspect_id == suspect_id))
    if character is None:
        raise HTTPException(status_code=404, detail="Postava nebyla nalezena.")

    return {
        "suspectId": character.suspect_id,
        "name": character.name,
        "description": character.description,
    }


@app.get("/api/history/{suspect_id}")
def api_history(suspect_id: str, db: Session = Depends(get_db)):
    character = db.scalar(select(Character).where(Character.suspect_id == suspect_id))
    if character is None:
        raise HTTPException(status_code=404, detail="Postava nebyla nalezena.")

    messages = db.scalars(
        select(Message)
        .where(Message.character_id == character.id)
        .order_by(Message.created_at.asc(), Message.id.asc())
    ).all()
    return {
        "character": {"suspectId": character.suspect_id, "name": character.name},
        "messages": [
            {
                "id": message.id,
                "sender": message.sender,
                "content": message.content,
                "createdAt": message.created_at.isoformat(),
            }
            for message in messages
        ],
    }


@app.post("/api/chat")
async def api_chat(
    suspect_id: str = Form(...),
    message: str = Form(...),
    db: Session = Depends(get_db),
):
    clean_message = message.strip()
    if not clean_message:
        raise HTTPException(status_code=400, detail="Zpráva nesmí být prázdná.")

    character = db.scalar(select(Character).where(Character.suspect_id == suspect_id))
    if character is None:
        raise HTTPException(status_code=404, detail="Postava nebyla nalezena.")

    game = db.scalar(select(GameSession).limit(1))
    if game is None:
        raise HTTPException(status_code=500, detail="Hra nebyla inicializována.")

    context_messages = db.scalars(
        select(Message)
        .where(Message.character_id == character.id)
        .order_by(Message.created_at.asc(), Message.id.asc())
    ).all()

    user_message = Message(game_session_id=game.id, character_id=character.id, sender="user", content=clean_message)
    db.add(user_message)
    db.commit()

    client = get_client()
    fallback_used = False
    try:
        completion = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": character.system_prompt},
                *[
                    {"role": "user" if item.sender == "user" else "assistant", "content": item.content}
                    for item in context_messages
                ],
                {"role": "user", "content": clean_message},
            ],
        )
        ai_text = completion.choices[0].message.content or "Promiň, teď nemohu odpovědět."
    except Exception:
        fallback_used = True
        ai_text = "Promiň, momentálně se nemohu spojit s AI službou."

    ai_message = Message(game_session_id=game.id, character_id=character.id, sender="assistant", content=ai_text.strip())
    db.add(ai_message)
    db.commit()

    return JSONResponse(
        {
            "character": {"suspectId": character.suspect_id, "name": character.name},
            "userMessage": {"id": user_message.id, "content": user_message.content},
            "assistantMessage": {"id": ai_message.id, "content": ai_message.content},
            "fallbackUsed": fallback_used,
        }
    )


class VerdictPayload(BaseModel):
    accused_name: str


@app.post("/verdict")
def verdict(payload: VerdictPayload, db: Session = Depends(get_db)):
    accused_name = payload.accused_name.strip()
    if not accused_name:
        raise HTTPException(status_code=400, detail="Jméno obviněného nesmí být prázdné.")

    character = db.scalar(select(Character).where(func.lower(Character.name) == accused_name.lower()))
    if character is None:
        raise HTTPException(status_code=404, detail="Podezřelý nebyl nalezen.")

    game = db.scalar(select(GameSession).limit(1))
    if game is None:
        raise HTTPException(status_code=500, detail="Hra nebyla inicializována.")

    if character.name == "Marek Kříž":
        won = True
        verdict_text = (
            "Vítězství. Odhalil jsi Marka Kříže. Marek chtěl zabránit prodeji Nexus AI, "
            "který by zničil jeho životní práci. V serverovně se s Viktorem pohádal, "
            "strhla se potyčka a Viktor zemřel uškrcením ethernetovým kabelem."
        )
    else:
        won = False
        verdict_text = (
            "Prohra. Obvinil jsi špatnou osobu. Skutečný vrah utekl, případ se rozpadl "
            "a ty jsi byl vyhozen od policie."
        )

    final_record = Message(
        game_session_id=game.id,
        character_id=character.id,
        sender="final_verdict",
        content=verdict_text,
    )
    db.add(final_record)
    db.commit()

    response_payload = {
        "won": won,
        "suspectId": character.suspect_id,
        "suspectName": character.name,
        "message": verdict_text,
    }

    reset_game_data(db)

    return response_payload
