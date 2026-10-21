from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import logging

# Initialize FastAPI App
app = FastAPI(title="ActionBridge AI API", version="1.0")

# Enable CORS for Frontend (Vite/React on localhost)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("actionbridge-backend")

# Pydantic Schemas
class ChatRequest(BaseModel):
    session_id: str
    text: str

# 1. Chat Endpoint
@app.post("/api/chat")
async def chat_endpoint(payload: ChatRequest):
    try:
        # Placeholder for agent.chat() integration
        return {
            "reply": f"Received: '{payload.text}'",
            "pending_change_id": None,
            "tool_calls": []
        }
    except Exception as e:
        logger.error(f"Error in /api/chat: {str(e)}")
        return {
            "reply": "⚠️ Encountered an issue processing your request. Please try again.",
            "pending_change_id": None,
            "error": str(e)
        }

# 2. Get Commitments
@app.get("/api/commitments")
async def get_commitments():
    try:
        return {"commitments": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 3. Get Plan
@app.get("/api/plan")
async def get_plan():
    try:
        return {"plan_items": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 4. Get Conflicts
@app.get("/api/conflicts")
async def get_conflicts():
    try:
        return {"conflicts": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 5. Get Change Details (Diff)
@app.get("/api/changes/{change_id}")
async def get_change_details(change_id: str):
    try:
        return {
            "change_id": change_id,
            "summary": "Sample diff summary",
            "diff": {}
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 6. Apply Change
@app.post("/api/changes/{change_id}/apply")
async def apply_change(change_id: str):
    try:
        return {"status": "success", "message": "Changes applied successfully."}
    except Exception as e:
        logger.error(f"Error applying change {change_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to apply changes: {str(e)}")

# 7. Cancel Change
@app.post("/api/changes/{change_id}/cancel")
async def cancel_change(change_id: str):
    try:
        return {"status": "cancelled", "message": "Changes discarded."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))