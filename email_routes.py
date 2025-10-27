from fastapi import APIRouter, HTTPException, BackgroundTasks, WebSocket, WebSocketDisconnect, Depends
from typing import List, Dict
from datetime import datetime, UTC
import json

from finops_stack.iam.auth_guard import get_user_with_roles
from finops_stack.api.schemas import (
    GmailConnectRequest,
    GmailConnectResponse,
    MonitorEmailRequest,
    MonitorEmailResponse,
    DownloadAttachmentRequest,
    BatchDownloadResponse,
    BatchDownloadRequest,
    BatchDownloadResponse,
    FileResponse,
    HandlerStatsResponse,
    RFPReportResponse
)
from finops_stack.finops_core.services.email_service import email_service
from finops_stack.finops_core.rfp_scout.composio_config import AUTH_CONFIG_ID, COMPOSIO_CALLBACK_URL

router = APIRouter()


# ============================================================================
# WebSocket Connection Manager
# ============================================================================

class ConnectionManager:
    """Manages WebSocket connections for real-time updates"""

    def __init__(self):
        self.active_connections: dict[str, List[WebSocket]] = {}

    async def connect(self, user_id: str, websocket: WebSocket):
        await websocket.accept()
        if user_id not in self.active_connections:
            self.active_connections[user_id] = []
        self.active_connections[user_id].append(websocket)

    def disconnect(self, user_id: str, websocket: WebSocket):
        if user_id in self.active_connections:
            self.active_connections[user_id].remove(websocket)
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]
    
    async def send_to_user(self, user_id: str, message: dict):
        """Send message to all connections for a specific user"""
        if user_id in self.active_connections:
            for connections in self.active_connections.values():
                for connection in connections:
                    try:
                        await connection.send_json(message)
                    except Exception:
                        pass

                    
manager = ConnectionManager()


# ============================================================================
# Gmail Connection Endpoints
# ============================================================================

@router.post("/connect-gmail", response_model=GmailConnectResponse)
async def connect_gmail_account(user_data: Dict = Depends(get_user_with_roles)):
    """
        Connect user's Gmail account via Composio OAuth
    
    **Process:**
    1. Generates OAuth URL for user to authenticate
    2. User visits URL and authorizes
    3. Returns connected account ID
        
    **Response:**
    - success: bool
    - connected_account_id: str (if successful)
    - redirect_url: str (URL for user to visit)
    - message: str
    """
    authenticated_user_id = user_data["user_id"]
    result = email_service.connect_gmail(
        user_id=authenticated_user_id,
        auth_config_id=AUTH_CONFIG_ID,
        callback_url=COMPOSIO_CALLBACK_URL
    )
    redirect_url = result.get("redirect_url")
    if not redirect_url:
        raise HTTPException(status_code=500, detail="Failed to get Gmail OAuth link")
    
    return GmailConnectResponse(
        success=True,
        connected_account_id=None,
        redirect_url=redirect_url,
        message="Visit this URL to authenticate your Gmail account."
    )
    # if not result.get("success"):
    #     raise HTTPException(
    #         status_code=500,
    #         detail = result.get("error", "Connection failed")
    #     )
    # return GmailConnectResponse(**result)

@router.get("/gmail-status")
async def get_gmail_status(user_data: Dict = Depends(get_user_with_roles)):
    """    
    Check Gmail connection status for a user
    
    **Path Parameters:**
    - user_id: User identifier
    
    **Response:**
    - user_id: str
    - handler_active: bool (whether handler exists for this user)
    - timestamp: str (ISO format)
    """
    user_id = user_data["user_id"]
    stats = email_service.get_handler_stats(user_id)

    return {
        "user_id": user_id,
        "handler_active": stats.get("handler_active", False),
        "connected": stats.get("handler_active", False),    # For compatibility
        "timestamp": datetime.now(UTC).isoformat()
    }

@router.post("/monitor", response_model=RFPReportResponse)
async def monitor_emails(request: MonitorEmailRequest, user_data: Dict = Depends(get_user_with_roles)):
    """
    Monitor Gmail inbox for RFP-related emails (synchronous)
    
    **Process:**
    1. Scans Gmail inbox for specified time period
    2. Identifies RFP-related emails using AI
    3. Extracts metadata and attachments info
    4. Optionally applies labels and downloads attachments
    5. Returns structured data
    
    **Request Body:**
    - user_id: Unique user identifier
    - hours_back: Hours to look back (1-168, default: 24)
    - apply_labels: Apply Gmail labels (default: false)
    - auto_download: Auto-download attachments (default: false)
    
    **Response:**
    - success: bool
    - total_rfp_emails: int
    - scan_period: str
    - timestamp: str
    - emails: List of email objects with metadata
    - labeled_emails: List (if labels applied)
    
    **Note:** This is synchronous and may take time. For long operations, 
    use /monitor-background endpoint instead.
    """
    user_id = user_data["user_id"]
    result = email_service.monitor_rfp_emails(
        user_id=user_id,
        hours_back=request.hours_back,
        apply_labels=request.apply_labels,
        auto_download=request.auto_download
    )

    # if not result.get("success"):
    #     raise HTTPException(
    #         status=500,
    #         detail=result.get("error", "Email monitoring failed")
    #     )
    
    return result
