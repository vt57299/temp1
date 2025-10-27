from typing import Dict, Optional, List
from pathlib import Path
from datetime import datetime, UTC
from fastapi import HTTPException

# from finops_stack.finops_core.rfp_scout.email_handler import RFPEmailHandler
from finops_stack.finops_core.rfp_scout.email_handler_cursor import RFPEmailHandler
from finops_stack.api.schemas import RFPReportResponse

class EmailService:
    """
    Pure business logic service for email operations
    """
    def __init__(self):
        self._handlers: Dict[str, RFPEmailHandler] = {}

    def _get_or_create_handler(self, user_id: str, download_dir: Optional[str] = None) -> RFPEmailHandler:
        """
        Get existing handler or create new one for user

        Args:
            user_id: Unique user identifier
            download dir: Optional custom download directory

        Returns:
            RFPEmailHandler instance for the user
        """
        if user_id not in self._handlers:
            if not download_dir:
                download_dir = f"./rfp_attachments/{user_id}"
            
            self._handlers[user_id] = RFPEmailHandler(
                user_id=user_id,
                download_dir=download_dir
            )
        return self._handlers[user_id]
    

    def connect_gmail(
        self,
        user_id: str,
        auth_config_id: str,
        callback_url: Optional[str] = None
    ) -> Dict:
        """
        Connect Gmail account for a user (non-blocking)

        Args:
            user_id: Unique user identifier
            auth_config_id: Composio auth config ID
            callback_url: OAuth callback URL

        Returns:
            {
                "success": bool,
                "redirect_url": str (URL to authenticate Gmail),
                "connection_request_id": str (used for polling later),
                "user_id": str,
                "message": str,
                "error": str (if failed)
            }
        """
        try:
            handler = self._get_or_create_handler(user_id)

            # Non-blocking connect call
            connection_data = handler.connect_gmail_account_nonblocking(
                auth_config_id=auth_config_id,
                callback_url=callback_url
            )

            return {
                "success": True,
                "redirect_url": connection_data["redirect_url"],
                "connection_request_id": connection_data.get("connection_request_id"),
                "user_id": user_id,
                "message": "Visit this URL to authenticate your Gmail account."
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "user_id": user_id,
                "message": "Failed to generate Gmail authentication link."
            }

    
    def monitor_rfp_emails(
            self,
            user_id: str,
            hours_back: int = 24,
            apply_labels: bool = False,
            auto_download: bool = False,
            max_results: int = 10
    ) -> RFPReportResponse:
        """
        Monitor Gmail inbox for RFP-related emails
        
        Args:
            user_id: Unique user identifier
            hours_back: Number of hours to look back (1-168)
            apply_labels: Whether to apply Gmail labels
            auto_download: wheter to automatically download attachments
            max_results: Maximum number of emails to fetch

        Returns:
            {
                "success": bool,
                "user_id": str,
                "total_rfp_emails": int,
                "scan_period": str,
                "timestamp": str,
                "emails": List[Dict],
                "labeled_emails": List[Dict] (if apply_labels=True),
                "error": str (if failed)
            }
        """
        try:
            # Validate hours_back
            if not (1 <= hours_back <= 168):
                return {
                    "success": False,
                    "error": "hours_back must be between 1 and 168",
                    "user_id": user_id,
                    "total_rfp_emails": 0,
                    "emails": []
                }
            handler = self._get_or_create_handler(user_id)
            result = handler.monitor_emails(
                hours_back=hours_back,
                apply_labels=apply_labels,
                auto_download=auto_download
            )

            # Ensure we’re working with a dict
            if not isinstance(result, dict):
                raise ValueError("CrewOutput did not return a dict — got: " + str(type(result)))

            # Defensive updates to keep data consistent
            total_emails = len(result.get("emails", []))
            result["total_rfp_emails"] = total_emails or result.get("total_rfp_emails", 0)
            result["scan_period"] = result.get("scan_period") or f"{hours_back} hours"
            result["timestamp"] = result.get("timestamp") or datetime.now(UTC).isoformat()

            # ✅ Build the Pydantic response model
            return RFPReportResponse(
                **result,
                success=True,
                error=None,
                user_id=user_id,
            )
        except Exception as exc:  # Return consistent envelope on failure
            # Convert HTTPException detail to string if present
            err = exc
            if isinstance(exc, HTTPException) and exc.detail is not None:
                err = exc.detail  # may be str or dict

            return RFPReportResponse(
                total_rfp_emails=0,
                scan_period=f"{hours_back} hours",
                timestamp=datetime.now(UTC).isoformat(),
                emails=[],
                success=False,
                error=str(err),
                user_id=user_id,
            )
    
    def download_attachment(
            self,
            user_id: str,
            email_id: str,
            attachment_id: str,
            filename: Optional[str] = None
    ) -> Optional[Path]:
        """
        Download a specific attachment
        
        Args:
            user_id: Unique user identifier
            email_id: Gmail email ID
            attachment_id: Gmail attachment ID
            filename: Optional filename
            
        Returns:
            Path to downloaded file or None if failed
        """
        try:
            handler = self._get_or_create_handler(user_id)

            file_path = handler.get_attachment(
                email_id=email_id,
                attachment_id=attachment_id,
                filename=filename
            )
            return file_path
        
        except Exception as e:
            print(f"❌ Error in download_attachment service: {str(e)}")
            return None
    
    def batch_download_attachments(
            self,
            user_id: str,
            email_data: Dict,
            priority_filter: Optional[List[str]] = None
    ) -> Dict:
        """
        Download all attachments from monitored emails
        
        Args:
            user_id: Unique user identifier
            email_data: Result from monitor_emails
            priority_filter: Optional list of priorities to filter
            
        Returns:
            {
                "success": bool,
                "user_id": str,
                "total_downloaded": int,
                "total_failed": int,
                "downloaded_files": Dict[str, List[Path]],
                "error": str (if failed)
            }
        """
        try:
            handler = self._get_or_create_handler(user_id)

            downloaded = handler.download_all_attachments(
                email_data=email_data,
                priority_filter=priority_filter
            )

            total_downloaded = sum(len(files) for files in downloaded.values())

            return {
                "success": True,
                "user_id": user_id,
                "total_downloaded": total_downloaded,
                "total_failed": 0,  # Handler doesn't return failed count
                "downloaded_files": downloaded
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "user_id": user_id,
                "total_downloaded": 0,
                "total_failed": 0,
                "downloaded_files": {}
            }
    
    def get_attachment_info(
            self,
            user_id: str,
            email_id: str
    ) -> Dict:
        """
                Get attachment information without downloading
        
        Args:
            user_id: Unique user identifier
            email_id: Gmail email ID
            
        Returns:
            {
                "success": bool,
                "email_id": str,
                "attachments": List[Dict],
                "error": str (if failed)
            }
        """
        try:
            handler = self._get_or_create_handler(user_id)

            attachments = handler.get_attachment_info(email_id)

            return {
                "success": True,
                "email_id": email_id,
                "attachments": attachments
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "email_id": email_id,
                "attachments": []
            }
    
    def get_handler_stats(self, user_id: str) -> Dict:
        """
                Get statistics about user's email handler
        
        Args:
            user_id: Unique user identifier
            
        Returns:
            {
                "user_id": str,
                "handler_active": bool,
                "download_directory": str (if active),
                "gmail_tools_count": int (if active)
        """
        if user_id not in self._handlers:
            return {
                "user_id": user_id,
                "handler_active": False,
                "message": "No active handler for this user"
            }
        
        handler = self._handlers[user_id]

        return {
            "user_id": user_id,
            "handler_active": True,
            "downloaded_directory": str(handler.download_dir.absolute()),
            "gmail_tools_count": len(handler.gmail_tools) if handler.gmail_tools else 0
        }
    
    def cleanup_handler(self, user_id: str) -> Dict:
        """
                Cleanup and remove handler for a user
        Useful for memory management
        
        Args:
            user_id: Unique user identifier
            
        Returns:
            {
                "success": bool,
                "user_id": str,
                "message": str
            }
        """
        if user_id in self._handlers:
            del self._handlers[user_id]
            return {
                "success": True,
                "user_id": user_id,
                "message": "Handler cleaned up successfully"
            }
        
        return {
            "success": False,
            "user_id": user_id,
            "message": "Handler not found"
        }
    
    def get_all_active_users(self) -> List[str]:
        """
        Get list of all users with active handlers
        
        Returns:
            List of user IDs
        """
        return list(self._handlers.keys())

# Global service instance
email_service = EmailService()
