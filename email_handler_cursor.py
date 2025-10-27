import os
from dotenv import load_dotenv
load_dotenv()

import re
from datetime import datetime, timedelta, UTC
from typing import List, Dict, Optional, Any
from composio import Composio
from composio_crewai import CrewAIProvider
from crewai import Agent, Task, Crew, Process
from langchain_openai import ChatOpenAI
import json
from pathlib import Path
import base64
from pydantic import BaseModel, Field, ValidationError
from fastapi import HTTPException
import requests


""" 
REFERENCE COMPOSIO TOOL EXECUTION:
----------------------------------
from composio import Composio

composio = Composio(api_key="your_api_key")
response = composio.tools.execute(
    "GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID",
    user_id="your_user_id",
    arguments=("message_id": "your_message_id")
)

if response.get("successful"):
    email_data = response["data"]
    subject = email_data.get("subject")
    sender = email_data.get("sender")
    body = email_data.get("messageText")
    print("Subject:", subject)
    print("Sender:", sender)
    print("Body:", body)
else:
    print("Error:", response.get("error"))

"""

# =============================
# Pydantic Output Specifications
# =============================


class AttachmentModel(BaseModel):
    filename: str
    type: Optional[str] = None
    size_kb: Optional[float] = Field(default=None, ge=0)
    attachment_id: str


class SenderModel(BaseModel):
    name: Optional[str] = None
    email: str


class EmailItemModel(BaseModel):
    email_id: str
    thread_id: Optional[str] = None
    sender: SenderModel
    subject: str
    received_date: str  # ISO timestamp as string; keep flexible for providers
    snippet: Optional[str] = None
    attachments: List[AttachmentModel] = Field(default_factory=list)
    deadline: Optional[str] = None
    organization: Optional[str] = None
    priority: str = Field(pattern=r"^(HIGH|MEDIUM|LOW)$")
    keywords_found: List[str] = Field(default_factory=list)


class RFPReportModel(BaseModel):
    total_rfp_emails: int = Field(ge=0)
    scan_period: str
    timestamp: str
    emails: List[EmailItemModel] = Field(default_factory=list)



# =========================
# Core Email Handler / Agent
# =========================


class RFPEmailHandler:
    """
    Handles RFP email monitoring and processing using CrewAI and Composio
    """
    
    def __init__(self, user_id: str = "default", download_dir: str = "./rfp_attachments", force_json_with_fallback: bool = True) -> None:
        """
        Initialize the RFP Email Handler

        Args:
            user_id: Unique identifier for the user (default: "default")
            download_dir: Directory to save downloaded attachments
            force_json_with_fallback: Try to enforce structured output and
                apply local fallback validation/repairs if needed.
        """
        self.user_id = user_id
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.force_json_with_fallback = force_json_with_fallback

        self.composio = Composio(
            api_key=os.getenv("COMPOSIO_API_KEY"),
            provider=CrewAIProvider()
        )
        self.openai_client = ChatOpenAI(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            temperature=0.2,
            max_completion_tokens=1000
        )
        self.gmail_tools = self._initialize_gmail_tools()
        self.email_agent = self._create_email_agent()

        print(f"📁 Attachment download directory: {self.download_dir.absolute()}")
    
    def connect_gmail_account(self, auth_config_id: str, callback_url: Optional[str] = None) -> str:
        """
        Generate connection URL for user to authenticate their Gmail account
        
        Args:
            auth_config_id: Your auth config ID from Composio dashboard
            callback_url: Optional callback URL after authentication
        
        Returns:
            redirect_url: URL for user to visit to authenticate
        """
        print(f"\n{'='*60}")
        print(f"🔗 INITIATING GMAIL CONNECTION")
        print(f"{'='*60}")
        print(f"👤 User ID: {self.user_id}")
        
        connection_request = self.composio.connected_accounts.link(
            user_id=self.user_id,
            auth_config_id=auth_config_id,
            callback_url=callback_url or 'https://localhost/callback'
        )
        
        redirect_url = connection_request.redirect_url
        print(f"\n🌐 Visit this URL to authenticate your Gmail account:")
        print(f"   {redirect_url}")
        print(f"\n⏳ Waiting for connection to be established...")
        
        try:
            connected_account = connection_request.wait_for_connection()
            print(f"✅ Connection successful!")
            print(f"📧 Connected Account ID: {connected_account.id}")
            print(f"{'='*60}\n")
            return connected_account.id
        except Exception as e:
            print(f"❌ Connection failed: {str(e)}")
            print(f"{'='*60}\n")
            raise
        
    def connect_gmail_account_nonblocking(self, auth_config_id: str, callback_url: Optional[str] = None) -> str:
        """
        Generate Gmail authentication URL (non-blocking version)

        This version only returns the redirect URL for the user to authenticate,
        without waiting for the connection to complete.

        Args:
            auth_config_id: Your auth config ID from Composio dashboard
            callback_url: Optional callback URL after authentication

        Returns:
            redirect_url: URL that user should visit to authenticate
        """
        print(f"\n{'='*60}")
        print(f"🔗 INITIATING GMAIL CONNECTION (NON-BLOCKING)")
        print(f"{'='*60}")
        print(f"👤 User ID: {self.user_id}")

        connection_request = self.composio.connected_accounts.link(
            user_id=self.user_id,
            auth_config_id=auth_config_id,
            callback_url=callback_url or 'https://localhost/callback'
        )

        redirect_url = connection_request.redirect_url
        print(f"\n🌐 Visit this URL to authenticate your Gmail account:")
        print(f"   {redirect_url}")
        print(f"{'='*60}\n")

        # Return both redirect URL and connection request ID (so you can poll later if needed)
        return {
            "redirect_url": redirect_url,
            "connection_request_id": connection_request.id
        }


    def _initialize_gmail_tools(self):
        """Initialize Gmail tools from Composio"""
        return self.composio.tools.get(
            user_id=self.user_id,
            toolkits=["GMAIL"]
        )
    
    def _create_email_agent(self):
        """Create the Email Handling Agent"""
        return Agent(
            role='Senior RFP Email Analyst',
            goal=(
                "Efficiently monitor Gmail for RFP-related emails, accurately identify genuine "
                "RFP opportunities, extract critical information, and prepare data for document processing"
            ),
            backstory=(
                "You are a seasoned procurement analyst with 10+ years of experience in handling RFPs across finance, "
                "IT, and consulting sectors. You have a keen eye for identifying genuine RFP opportunities and "
                "can quickly assess their relevance and priority. You understand the urgency and importance "
                "of timely RFP responses and ensure no opportunity is missed."
            ),
            tools=self.gmail_tools,
            verbose=True,
            allow_delegation=False,
            max_iter=3,
            llm=self.openai_client
        )
    
    def create_email_fetch_task(self, hours_back: int = 24, max_results: int = 10):
        """
        Create task to fetch and analyze RFP emails with structured output.
        """
        if Task is None:
            raise HTTPException(status_code=500, detail="CrewAI not available at runtime.")

        since_date = (datetime.now(UTC) -
                      timedelta(hours=hours_back)).strftime("%Y/%m/%d")

        description = f"""
        Search Gmail for RFP-related emails from the last {hours_back} hours (limit to {max_results} emails):

        Search Criteria:
        1. Keywords in subject or body: "RFP", "Request for Proposal", "Tender", "Bid", "ITB", "RFQ"
        2. Emails with attachments (PDF, DOCX, XLSX, DOC, XLS)
        3. Exclude: spam, promotional emails, auto-replies

        For Each Valid RFP Email, Extract:
        - Email ID and Thread ID
        - Sender name and email address
        - Subject line
        - Date and time received
        - Email body snippet (first 200 characters)
        - List of all attachments with their IDs (name, type, size, attachment_id)
        - Any deadline or submission date mentioned
        - Company/organization name if mentioned
        - Priority indicators (urgent, deadline within 7 days)

        CRITICAL: Include attachment_id for each attachment - this is required for downloading

        Categorize Priority:
        - HIGH: Deadline mentioned within 7 days or contains "urgent"
        - MEDIUM: Deadline within 14 days
        - LOW: No specific deadline or deadline beyond 14 days

        Output Format:
        Provide a JSON-structured report with all findings.

        Gmail Query to use (example):
        (RFP OR "Request for Proposal" OR Tender OR Bid OR ITB OR RFQ) has:attachment after:{since_date}
        """

        expected_output = (
            "A JSON that matches the RFPReportModel schema exactly. Do not include commentary."
        )

        # Enforce structured output via Pydantic model
        task = Task(
            description=description,
            agent=self.email_agent,
            expected_output=expected_output,
            output_pydantic=RFPReportModel,  # type: ignore[arg-type]
        )
        return task
        
    def create_label_task(self) -> Task:
        """Create task to label processed RFP emails"""
        label_colors = {
            "RFP_High": {"background_color": "#fb4c2f", "text_color": "#000000"},   # VERMILION, BLACK
            "RFP_Medium": {"background_color": "#ffad47", "text_color": "#000000"}, # NEON_CARROT, BLACK
            "RFP_Low": {"background_color": "#34a853", "text_color": "#ffffff"},    # APPLE, WHITE
        }
        return Task(
            description=f"""
            For all identified RFP emails:
            1. Create a Gmail label "RFP_Processed" if it doesn't exist.
            2. Apply this label to each processed email.
            3. Create priority-based labels ONLY using these colors:
            - "RFP_High": background {label_colors['RFP_High']['background_color']}, text {label_colors['RFP_High']['text_color']}
            - "RFP_Medium": background {label_colors['RFP_Medium']['background_color']}, text {label_colors['RFP_Medium']['text_color']}
            - "RFP_Low": background {label_colors['RFP_Low']['background_color']}, text {label_colors['RFP_Low']['text_color']}
            4. DO NOT use any other colors for label creation.
            5. Apply appropriate priority labels.

            Only use the above color codes. Do not generate or select any other colors.
            **IMPORTANT:**
            Your final output must be a valid JSON object and set as `output_json`.
            Example:
            {{
                "labeled_emails": [
                    {{ "email_id": "...", "labels_applied": ["RFP_Processed", "RFP_High"] }},
                    ...
                ]
            }}
            """,
            agent=self.email_agent,
            expected_output="A valid JSON object listing all emails that were labeled successfully, set as output_json."
        )
    
    def monitor_emails(self, hours_back: int = 24, apply_labels: bool = False, auto_download: bool = False) -> RFPReportModel:
        """
        Main method to monitor and process RFP emails
        
        Args:
            hours_back: How many hours back to search
            apply_labels: Whether to apply Gmail labels to processed emails
            auto_download: Wheter to automatically download all attachments
        
        Returns:
            Dictionary containing processed email data
        """
        if Crew is None or Task is None or Agent is None:
            raise HTTPException(status_code=500, detail="CrewAI is not available at runtime.")
        
        # print(f"\n{'='*60}")
        # print(f"🔍 RFP EMAIL MONITORING STARTED")
        # print(f"{'='*60}")
        # print(f"📅 Scanning last {hours_back} hours")
        # print(f"🏷️  Auto-labeling: {'Enabled' if apply_labels else 'Disabled'}")
        # print(f"📥 Auto-download: {'Enabled' if auto_download else 'Disabled'}")
        # print(f"{'='*60}\n")
        
        # Create tasks
        task = self.create_email_fetch_task(hours_back=hours_back)
        # if apply_labels:
        #     tasks.append(self.create_label_task())
        
        # Create and run crew
        crew = Crew(
            agents=[self.email_agent],
            tasks=[task],
            # process=Process.sequential,
            verbose=True
        )
        
        result = crew.kickoff()

        try:
            result_dict = result.to_dict()
            if not result_dict or "emails" not in result_dict:
                print("⚠️  No valid emails found in CrewAI result.")
                return {"emails": []}
            
            print(f"\n📨 Total emails fetched: {len(result_dict.get('emails', []))}")

            if auto_download:
                print("\n🚀 Starting automatic attachment download...")
                downloaded = self.download_all_attachments(
                    email_data=result_dict
                )
                # result_dict["downloaded_files"] = downloaded

            if apply_labels:
                print("🏷️  Label application feature not yet implemented.\n")
                # TODO: implement label tagging via Composio Gmail actions

            print("\n✅ Email monitoring completed successfully.")
            return result_dict

        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Email monitoring failed: {exc}")

    
    def get_attachment(
        self, 
        email_id: str, 
        attachment_id: str, 
        filename: Optional[str] = None,
        save_path: Optional[str] = None
    ) -> Optional[Path]:
        """
        Download a specific attachment from an email using Composio's Gmail action
        
        Args:
            email_id: Gmail email ID (message ID)
            attachment_id: Gmail attachment ID
            filename: Original filename of the attachment
            save_path: Custom path to save the file (optional)
        
        Returns:
            Path: Path object of the saved file, or None if download failed
        """
        try:
            print(f"📎 Downloading attachment from email {email_id}...")
            if filename:
                print(f"   Filename: {filename}")
            
            # Execute the Gmail get attachment action using Composio
            response = self.composio.tools.execute(
                "GMAIL_GET_ATTACHMENT",
                user_id=self.user_id,
                arguments={
                    "message_id": email_id,
                    "attachment_id": attachment_id,
                    "file_name": filename or f"attachment_{attachment_id}"
                }
            )
            
            # Check if response is successful
            if not response or not response.get("successful"):
                print(f"❌ Error downloading attachment: {response.get('error') if response else 'No response'}")
                return None

            file_info = response["data"]["file"]
            download_url = file_info.get("s3url")
            file_name = file_info.get("name") or filename or f"attachment_{attachment_id}"
            mime_type = file_info.get("mimetype")

            if not download_url:
                print("❌ No download URL found in response.")
                return None

            # Determine file save location
            if save_path:
                file_path = Path(save_path)
            else:
                email_dir = self.download_dir / f"email_{email_id[:8]}"
                email_dir.mkdir(parents=True, exist_ok=True)
                file_path = email_dir / self._sanitize_filename(file_name)

            # Download the file from S3 URL
            print(f"⬇️ Fetching from: {download_url}")
            res = requests.get(download_url)
            res.raise_for_status()

            # Save file locally
            file_path.write_bytes(res.content)
            file_size_kb = len(res.content) / 1024

            print(f"✅ Downloaded: {file_path.name} ({file_size_kb:.2f} KB)")
            print(f"   MIME type: {mime_type}")
            print(f"   Saved to: {file_path.absolute()}")

            return file_path

        except Exception as e:
            print(f"❌ Error downloading attachment: {str(e)}")
            import traceback
            traceback.print_exc()
            return None

    def download_all_attachments(
        self, 
        email_data: Dict,
        priority_filter: Optional[List[str]] = None
    ) -> Dict[str, List[Path]]:
        """
        Download all attachments from the email monitoring results
        
        Args:
            email_data: The result dictionary from monitor_emails()
            priority_filter: Optional list of priorities to filter (e.g., ["HIGH", "MEDIUM"])
        
        Returns:
            Dictionary mapping email_id to list of downloaded file paths
        """
        if not isinstance(email_data, dict) or "emails" not in email_data:
            print("❌ Invalid email data format")
            return {}
        
        downloaded_files = {}
        total_downloaded = 0
        total_failed = 0
        
        print(f"\n{'='*60}")
        print(f"📥 BATCH DOWNLOAD STARTED")
        print(f"{'='*60}")
        if priority_filter:
            print(f"🎯 Priority filter: {', '.join(priority_filter)}")
        print()
        
        for email in email_data["emails"]:
            email_id = email.get("email_id")
            priority = email.get("priority")
            subject = email.get("subject", "No subject")
            attachments = email.get("attachments", [])
            
            # Apply priority filter if specified
            if priority_filter and priority not in priority_filter:
                continue
            
            if not email_id or not attachments:
                continue
            
            print(f"\n📧 Email: {subject[:50]}...")
            print(f"   Priority: {priority}")
            print(f"   Attachments: {len(attachments)}")
            
            email_files = []
            
            for idx, attachment in enumerate(attachments, 1):
                attachment_id = attachment.get("attachment_id")
                filename = attachment.get("filename")
                
                if not attachment_id:
                    print(f"   ⚠️  [{idx}/{len(attachments)}] Skipping {filename}: No attachment_id")
                    total_failed += 1
                    continue
                
                print(f"   📎 [{idx}/{len(attachments)}] Downloading: {filename}")
                
                try:
                    saved_path = self.get_attachment(email_id, attachment_id, filename)
                    if saved_path:
                        email_files.append(saved_path)
                        total_downloaded += 1
                    else:
                        total_failed += 1
                except Exception as e:
                    print(f"   ❌ Failed: {str(e)}")
                    total_failed += 1
            
            if email_files:
                downloaded_files[email_id] = email_files
        
        print(f"\n{'='*60}")
        print(f"📊 DOWNLOAD SUMMARY")
        print(f"{'='*60}")
        print(f"✅ Successfully downloaded: {total_downloaded} files")
        print(f"❌ Failed downloads: {total_failed} files")
        print(f"📁 Emails processed: {len(downloaded_files)}")
        print(f"💾 Save location: {self.download_dir.absolute()}")
        print(f"{'='*60}\n")
        
        return downloaded_files

    def _sanitize_filename(self, filename: str) -> str:
        """
        Sanitize filename to be safe for filesystem
        
        Args:
            filename: Original filename

        Returns:
            Sanitized filename 
        """
        # Remove or replace unsafe characters
        unsafe_chars = '<>:"/\\|?*'
        for char in unsafe_chars:
            filename = filename.replace(char, '_')

        # Limit length
        max_length = 200
        if len(filename) > max_length:
            name, ext = os.path.splitext(filename)
            filename = name[:max_length - len(ext)] + ext
            
        return filename
    
    def get_attachment_info(self, email_id: str) -> List[Dict]:
        """
        Get information about all attachments in an email without downloading

        Args:
            email_id: Gmail email ID
        """
        try:
            response = self.composio.tools.execute(
                "GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID",                # Maybe we can use "GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID" as slug. "message_id" is required in the arguments for this.
                user_id=self.user_id,
                arguments={"message_id": email_id}    # If error, try changing "email_id" to "message_id"80
            )

            if not response or not hasattr(response, 'data'):
                return []
            
            # Extract attachment information from email data
            email_data = response.data
            attachments = []

            # Parse the email payload for attachments
            

        except Exception as e:
            print(f"❌ Error getting attachment info: {str(e)}")
            return []

