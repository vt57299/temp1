"""
FastAPI service to monitor Gmail for RFP emails using CrewAI + Composio,
with enforced structured JSON output and robust fallbacks.

This module defines:
- Pydantic models describing the desired JSON output
- An RFPEmailHandler that sets up the CrewAI agent and task
- Strict structured output via Task.output_pydantic
- Fallback JSON extraction/repair and Pydantic validation
- A FastAPI endpoint that returns validated JSON
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, ValidationError


# Optional third-party dependencies; guard imports for runtime resilience
try:
    # LangChain OpenAI wrapper
    from langchain_openai import ChatOpenAI  # type: ignore
except Exception:  # pragma: no cover - missing optional dependency at runtime
    ChatOpenAI = None  # type: ignore

try:
    # CrewAI core
    from crewai import Agent, Task, Crew  # type: ignore
except Exception:  # pragma: no cover - missing optional dependency at runtime
    Agent = Task = Crew = None  # type: ignore

try:
    # Composio (tools orchestration for Gmail)
    from composio_crewai import ComposioToolSet as Composio  # type: ignore
    from composio_crewai import CrewAIProvider  # type: ignore
except Exception:  # pragma: no cover - missing optional dependency at runtime
    Composio = CrewAIProvider = None  # type: ignore


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


class RFPReportResponse(RFPReportModel):
    """Envelope response that preserves existing fields and adds metadata."""
    success: bool
    error: Optional[str] = None
    user_id: str


# =====================================
# JSON Extraction / Repair Helper Utils
# =====================================


_FENCED_BLOCK_RE = re.compile(
    r"```(?:json)?\s*(\{[\s\S]*?\})\s*```",
    re.IGNORECASE,
)

_FIRST_BRACE_RE = re.compile(r"\{[\s\S]*\}")


def _extract_json_candidates(text: str) -> List[str]:
    candidates: List[str] = []
    for match in _FENCED_BLOCK_RE.finditer(text):
        block = match.group(1)
        if block:
            candidates.append(block.strip())
    if not candidates:
        brace_match = _FIRST_BRACE_RE.search(text)
        if brace_match:
            candidates.append(brace_match.group(0).strip())
    return candidates


def _json_loads_safely(candidate: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(candidate)
    except Exception:
        return None


def _attempt_basic_json_repairs(text: str) -> Optional[Dict[str, Any]]:
    """
    Best-effort quick repairs without extra dependencies.
    - Extract JSON code-fences or first-brace block
    - Remove common trailing notes like 'Thought:' and 'Action:' outside braces
    - Try removing trailing commas
    """
    candidates = _extract_json_candidates(text)
    for cand in candidates:
        parsed = _json_loads_safely(cand)
        if parsed is not None:
            return parsed

    # Remove trailing commas (simple heuristic) and retry
    for cand in candidates:
        repaired = re.sub(r",\s*([}\]])", r"\1", cand)
        parsed = _json_loads_safely(repaired)
        if parsed is not None:
            return parsed

    return None


def _validate_or_raise(data: Dict[str, Any]) -> RFPReportModel:
    try:
        return RFPReportModel.model_validate(data)
    except ValidationError as exc:  # surface validation issue cleanly
        raise HTTPException(status_code=502, detail={
            "error": "Response validation failed",
            "issues": json.loads(exc.json()),
        })


def _extract_balanced_json_after_marker(text: str, marker: str = "Final Output:") -> Optional[Dict[str, Any]]:
    """
    Find the first balanced JSON object following a marker (e.g., "Final Output:").
    This is robust against nested braces and quoted braces.
    """
    start_pos = text.find(marker)
    if start_pos == -1:
        start_pos = 0
    s = text[start_pos:]
    brace_start = s.find("{")
    if brace_start == -1:
        return None

    i = brace_start
    depth = 0
    in_string = False
    escape = False
    end_index: Optional[int] = None

    for j in range(i, len(s)):
        ch = s[j]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                end_index = j
                break

    if end_index is None:
        return None

    candidate = s[i:end_index + 1]
    return _json_loads_safely(candidate)


# =========================
# Core Email Handler / Agent
# =========================


class RFPEmailHandler:
    """
    Handles RFP email monitoring and processing using CrewAI and Composio
    """

    def __init__(
        self,
        user_id: str = "default",
        download_dir: str = "./rfp_attachments",
        force_json_with_fallback: bool = True,
    ) -> None:
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

        # Optional integrations
        if Composio is None or CrewAIProvider is None:
            self.composio = None
        else:
            self.composio = Composio(
                api_key=os.getenv("COMPOSIO_API_KEY"),
                provider=CrewAIProvider(),
            )

        if ChatOpenAI is None:
            self.openai_client = None
        else:
            # Favor small, fast, JSON-friendly model
            self.openai_client = ChatOpenAI(
                model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                temperature=0.2,
                max_completion_tokens=1200,
            )

        self.gmail_tools = self._initialize_gmail_tools()
        self.email_agent = self._create_email_agent()

        print(f"📁 Attachment download directory: {self.download_dir.absolute()}")

    def _initialize_gmail_tools(self):
        """Initialize Gmail tools from Composio."""
        if self.composio is None:
            return []
        return self.composio.tools.get(
            user_id=self.user_id,
            toolkits=["GMAIL"],
        )

    def _create_email_agent(self):
        """Create the Email Handling Agent."""
        if Agent is None:
            return None
        return Agent(
            role="Senior RFP Email Analyst",
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
            llm=self.openai_client,
        )

    def create_email_fetch_task(self, hours_back: int = 24, max_results: int = 10):
        """
        Create task to fetch and analyze RFP emails with structured output.
        """
        if Task is None:
            raise HTTPException(status_code=500, detail="CrewAI not available at runtime.")

        since_date = (datetime.now(timezone.utc) -
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

    def run_monitor(self, hours_back: int = 24, max_results: int = 10) -> RFPReportModel:
        if Crew is None or Task is None or Agent is None:
            raise HTTPException(status_code=500, detail="CrewAI is not available at runtime.")

        task = self.create_email_fetch_task(hours_back=hours_back, max_results=max_results)
        crew = Crew(agents=[self.email_agent], tasks=[task], verbose=True)
        result = crew.kickoff()

        # Try to get structured object directly from task output when output_pydantic is used
        try:
            # Some CrewAI versions attach parsed pydantic to task.output or result.pydantic
            maybe_obj = getattr(task, "output", None) or getattr(result, "pydantic", None)
            if isinstance(maybe_obj, RFPReportModel):
                return maybe_obj
            # Accept dict or BaseModel-like and validate
            if isinstance(maybe_obj, dict):
                return _validate_or_raise(maybe_obj)
            if isinstance(maybe_obj, BaseModel):  # type: ignore[arg-type]
                return _validate_or_raise(maybe_obj.model_dump())  # type: ignore[assignment]
        except Exception:
            pass

        # Fallback: parse raw output
        raw_text: str = str(result) if not isinstance(result, str) else result
        # Prefer balanced extraction after the common "Final Output:" marker
        parsed = _extract_balanced_json_after_marker(raw_text)
        if parsed is None:
            parsed = _attempt_basic_json_repairs(raw_text)
        if parsed is not None:
            return _validate_or_raise(parsed)

        # Final fallback: if we have an llm client, ask it to emit strict JSON only
        if self.openai_client is not None:
            try:
                # Use LangChain structured output if available
                structured_llm = self.openai_client.with_structured_output(RFPReportModel)  # type: ignore[attr-defined]
                coerced = structured_llm.invoke({
                    "instruction": (
                        "Coerce the following text into the strictly valid JSON that matches "
                        "the RFPReportModel schema. If information is missing, leave fields null "
                        "or use empty lists. Do not add speculative data."
                    ),
                    "text": raw_text,
                })
                if isinstance(coerced, RFPReportModel):
                    return coerced
            except Exception:
                # If structured_output isn't available, try plain prompt + regex extraction
                pass

        # If all else fails, raise a helpful error
        raise HTTPException(status_code=502, detail={
            "error": "Agent did not return valid JSON and coercion failed",
            "sample": raw_text[:1200],
        })


# ===============
# FastAPI wiring
# ===============


app = FastAPI(title="RFP Email Monitoring Service", version="1.0.0")


class MonitorRequest(BaseModel):
    hours_back: int = Field(default=24, ge=1, le=24 * 30)
    max_results: int = Field(default=10, ge=1, le=50)
    user_id: str = Field(default="me")
    auto_download: bool = Field(default=False)


@app.post("/rfp/monitor", response_model=RFPReportResponse)
def monitor_emails(req: MonitorRequest) -> RFPReportResponse:
    handler = RFPEmailHandler(user_id=req.user_id)
    try:
        report = handler.run_monitor(hours_back=req.hours_back, max_results=req.max_results)

        # Ensure total consistency before returning
        if report.total_rfp_emails != len(report.emails):
            report.total_rfp_emails = len(report.emails)
        if not report.scan_period:
            report.scan_period = f"{req.hours_back} hours"
        if not report.timestamp:
            report.timestamp = datetime.now(timezone.utc).isoformat()

        return RFPReportResponse(
            **report.model_dump(),
            success=True,
            error=None,
            user_id=req.user_id,
        )
    except Exception as exc:  # Return consistent envelope on failure
        # Convert HTTPException detail to string if present
        err = exc
        if isinstance(exc, HTTPException) and exc.detail is not None:
            err = exc.detail  # may be str or dict

        return RFPReportResponse(
            total_rfp_emails=0,
            scan_period=f"{req.hours_back} hours",
            timestamp=datetime.now(timezone.utc).isoformat(),
            emails=[],
            success=False,
            error=str(err),
            user_id=req.user_id,
        )


# For local debugging: `uvicorn main:app --reload`
if __name__ == "__main__":  # pragma: no cover
    try:
        import uvicorn  # type: ignore

        uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=True)
    except Exception:  # If uvicorn isn't available in env
        print("Run with: pip install fastapi uvicorn pydantic crewai composio-crewai langchain-openai")


