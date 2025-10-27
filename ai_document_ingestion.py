import os
import json
from pathlib import Path
from typing import Dict, List, Optional, Union
from datetime import datetime
import mimetypes
from io import BytesIO

# PDF and image processing
import pymupdf
import pytesseract
from PIL import Image

# Word document processing
import docx


class DocumentIngestionAgent:
    """
    Core agent for ingesting and processing RFP documents.
    Handles multiple file formats and extracts structured text data.
    """
    
    def __init__(self, upload_base_dir: str):
        """
        Initialize the Document Ingestion Agent.
        
        Args:
            upload_base_dir: Base directory where uploaded files are stored
        """
        self.upload_base_dir = Path(upload_base_dir)
        self.supported_formats = {
            'pdf': self._process_pdf,
            'docx': self._process_docx,
            'doc': self._process_docx,  # Try docx library for .doc files too
            'txt': self._process_txt,
            'image': self._process_image,
        }
    
    def ingest_document(
        self,
        file_path: Union[str, Path],
        file_id: str,
        metadata: Optional[Dict] = None
    ) -> Dict:
        """
        Main method to ingest a document and extract its content.
        
        Args:
            file_path: Path to the uploaded file
            file_id: Unique identifier for the file
            metadata: Additional metadata about the file
            
        Returns:
            Dict containing extracted text and metadata
        """
        file_path = Path(file_path)
        
        if not file_path.exists():
            return {
                "success": False,
                "file_id": file_id,
                "error": "File not found",
                "extracted_text": None,
                "pages": []
            }
        
        # Read file bytes
        try:
            with open(file_path, 'rb') as f:
                file_bytes = f.read()
        except Exception as e:
            return {
                "success": False,
                "file_id": file_id,
                "error": f"Failed to read file: {str(e)}",
                "extracted_text": None,
                "pages": []
            }
        
        # Determine file type
        file_type = self._determine_file_type(file_path)
        
        # Process based on file type
        try:
            extracted_data = self._process_file(file_bytes, file_path.name, file_type)
            
            # Combine all page texts
            combined_text = "\n\n".join([page["text"] for page in extracted_data["pages"]])
            
            # Prepare response
            result = {
                "success": True,
                "file_id": file_id,
                "file_name": file_path.name,
                "file_type": file_type,
                "extracted_text": combined_text,
                "pages": extracted_data["pages"],
                "page_count": len(extracted_data["pages"]),
                "word_count": len(combined_text.split()),
                "char_count": len(combined_text),
                "extraction_method": extracted_data.get("method", "unknown"),
                "metadata": metadata or {},
                "processed_at": datetime.now().isoformat(),
                "ocr_used": extracted_data.get("ocr_used", False)
            }
            
            return result
            
        except Exception as e:
            return {
                "success": False,
                "file_id": file_id,
                "error": str(e),
                "extracted_text": None,
                "pages": []
            }
    
    def ingest_email_data(
        self,
        email_data: Dict
    ) -> Dict:
        """
        Process email data from Email Handling Agent.
        
        Args:
            email_data: Dictionary containing email content and metadata
            
        Returns:
            Dict containing processed email data
        """
        try:
            # Extract relevant email information
            formatted_text = self._format_email_text(email_data)
            
            processed_data = {
                "success": True,
                "source": "email",
                "extracted_text": formatted_text,
                "pages": [{"page": 1, "text": formatted_text}],
                "page_count": 1,
                "metadata": {
                    "from": email_data.get("from", ""),
                    "subject": email_data.get("subject", ""),
                    "date": email_data.get("date", ""),
                    "to": email_data.get("to", ""),
                    "has_attachments": email_data.get("has_attachments", False)
                },
                "processed_at": datetime.now().isoformat()
            }
            
            return processed_data
            
        except Exception as e:
            return {
                "success": False,
                "source": "email",
                "error": str(e),
                "extracted_text": None,
                "pages": []
            }
    
    def batch_ingest(
        self,
        file_list: List[Dict],
        email_data_list: Optional[List[Dict]] = None
    ) -> Dict:
        """
        Process multiple documents and email data in batch.
        
        Args:
            file_list: List of file dictionaries with file_path, file_id, metadata
            email_data_list: Optional list of email data dictionaries
            
        Returns:
            Dict containing all processed data ready for RFP Summary Agent
        """
        results = {
            "documents": [],
            "emails": [],
            "summary": {
                "total_documents": len(file_list),
                "successful_documents": 0,
                "failed_documents": 0,
                "total_emails": len(email_data_list) if email_data_list else 0,
                "successful_emails": 0,
                "failed_emails": 0,
                "total_pages": 0
            },
            "processed_at": datetime.now().isoformat()
        }
        
        # Process documents
        for file_info in file_list:
            result = self.ingest_document(
                file_path=file_info["file_path"],
                file_id=file_info["file_id"],
                metadata=file_info.get("metadata")
            )
            results["documents"].append(result)
            
            if result["success"]:
                results["summary"]["successful_documents"] += 1
                results["summary"]["total_pages"] += result.get("page_count", 0)
            else:
                results["summary"]["failed_documents"] += 1
        
        # Process emails
        if email_data_list:
            for email_data in email_data_list:
                result = self.ingest_email_data(email_data)
                results["emails"].append(result)
                
                if result["success"]:
                    results["summary"]["successful_emails"] += 1
                else:
                    results["summary"]["failed_emails"] += 1
        
        # Combine all extracted text
        results["combined_text"] = self._combine_all_text(results)
        results["all_pages"] = self._combine_all_pages(results)
        
        return results
    
    # ============ Private Methods ============
    
    def _determine_file_type(self, file_path: Path) -> str:
        """Determine the type of file based on extension and MIME type."""
        extension = file_path.suffix.lower().lstrip('.')
        mime_type, _ = mimetypes.guess_type(str(file_path))
        
        if extension in ['pdf']:
            return 'pdf'
        elif extension in ['docx']:
            return 'docx'
        elif extension in ['doc']:
            return 'doc'
        elif extension in ['txt', 'text']:
            return 'txt'
        elif extension in ['png', 'jpg', 'jpeg', 'tiff', 'bmp', 'gif'] or \
             (mime_type and mime_type.startswith('image/')):
            return 'image'
        else:
            return 'unknown'
    
    def _process_file(self, file_bytes: bytes, filename: str, file_type: str) -> Dict:
        """Route file to appropriate processing method."""
        processor = self.supported_formats.get(file_type)
        
        if processor:
            return processor(file_bytes, filename)
        else:
            raise Exception(f"Unsupported file type: {file_type}")
    
    def _process_pdf(self, file_bytes: bytes, filename: str) -> Dict:
        """
        Extract text from PDF files using pymupdf.
        Falls back to OCR for image-based PDFs.
        """
        doc = pymupdf.open(stream=file_bytes, filetype="pdf")
        extracted_pages = []
        ocr_used = False
        
        for i, page in enumerate(doc):
            text = page.get_text()
            
            # If no text found, use OCR
            if not text.strip():
                pix = page.get_pixmap()  # Render page to an image
                img = Image.open(BytesIO(pix.tobytes("png")))
                text = pytesseract.image_to_string(img)
                ocr_used = True
            
            extracted_pages.append({"page": i + 1, "text": text})
        
        doc.close()
        
        return {
            "pages": extracted_pages,
            "method": "pymupdf",
            "ocr_used": ocr_used
        }
    
    def _process_docx(self, file_bytes: bytes, filename: str) -> Dict:
        """Extract text from DOCX/DOC files."""
        try:
            doc = docx.Document(BytesIO(file_bytes))
            text = "\n".join([paragraph.text for paragraph in doc.paragraphs])
            
            return {
                "pages": [{"page": 1, "text": text}],
                "method": "docx_extraction",
                "ocr_used": False
            }
        except Exception as e:
            raise Exception(f"DOCX/DOC processing failed: {str(e)}")
    
    def _process_txt(self, file_bytes: bytes, filename: str) -> Dict:
        """Extract text from TXT files."""
        try:
            # Try UTF-8 first
            text = file_bytes.decode('utf-8')
        except UnicodeDecodeError:
            # Fallback to latin-1
            try:
                text = file_bytes.decode('latin-1')
            except:
                text = file_bytes.decode('utf-8', errors='ignore')
        
        return {
            "pages": [{"page": 1, "text": text}],
            "method": "text_file",
            "ocr_used": False
        }
    
    def _process_image(self, file_bytes: bytes, filename: str) -> Dict:
        """Extract text from images using OCR."""
        try:
            image = Image.open(BytesIO(file_bytes))
            text = pytesseract.image_to_string(image)
            
            return {
                "pages": [{"page": 1, "text": text}],
                "method": "ocr_image",
                "ocr_used": True
            }
        except Exception as e:
            raise Exception(f"Image OCR failed: {str(e)}")
    
    def _format_email_text(self, email_data: Dict) -> str:
        """Format email data into structured text."""
        formatted = []
        
        formatted.append(f"Email Subject: {email_data.get('subject', 'N/A')}")
        formatted.append(f"From: {email_data.get('from', 'N/A')}")
        formatted.append(f"To: {email_data.get('to', 'N/A')}")
        formatted.append(f"Date: {email_data.get('date', 'N/A')}")
        formatted.append("\nEmail Body:")
        formatted.append(email_data.get('body', ''))
        
        if email_data.get('attachments_info'):
            formatted.append("\nAttachments:")
            for att in email_data['attachments_info']:
                formatted.append(f"- {att}")
        
        return "\n".join(formatted)
    
    def _combine_all_text(self, results: Dict) -> str:
        """Combine all extracted text from documents and emails."""
        combined = []
        
        # Add email texts
        for email in results["emails"]:
            if email["success"] and email["extracted_text"]:
                combined.append("=== EMAIL ===")
                combined.append(email["extracted_text"])
                combined.append("")
        
        # Add document texts
        for doc in results["documents"]:
            if doc["success"] and doc["extracted_text"]:
                combined.append(f"=== DOCUMENT: {doc['file_name']} ===")
                combined.append(doc["extracted_text"])
                combined.append("")
        
        return "\n".join(combined)
    
    def _combine_all_pages(self, results: Dict) -> List[Dict]:
        """Combine all pages from all documents and emails."""
        all_pages = []
        
        # Add email pages
        for email in results["emails"]:
            if email["success"] and email.get("pages"):
                for page in email["pages"]:
                    all_pages.append({
                        "source": "email",
                        "source_metadata": email.get("metadata", {}),
                        **page
                    })
        
        # Add document pages
        for doc in results["documents"]:
            if doc["success"] and doc.get("pages"):
                for page in doc["pages"]:
                    all_pages.append({
                        "source": "document",
                        "source_file": doc["file_name"],
                        "file_id": doc["file_id"],
                        **page
                    })
        
        return all_pages


# ============ Helper Functions for Integration ============

def create_ingestion_agent(upload_base_dir: str) -> DocumentIngestionAgent:
    """
    Factory function to create a DocumentIngestionAgent instance.
    
    Args:
        upload_base_dir: Base directory for uploaded files
        
    Returns:
        Configured DocumentIngestionAgent instance
    """
    return DocumentIngestionAgent(upload_base_dir)


def process_single_document(
    agent: DocumentIngestionAgent,
    file_path: str,
    file_id: str,
    metadata: Optional[Dict] = None
) -> Dict:
    """
    Convenience function to process a single document.
    
    Args:
        agent: DocumentIngestionAgent instance
        file_path: Path to the file
        file_id: Unique file identifier
        metadata: Optional metadata dictionary
        
    Returns:
        Processing result dictionary
    """
    return agent.ingest_document(file_path, file_id, metadata)


def process_user_documents(
    agent: DocumentIngestionAgent,
    user_id: str,
    file_ids: List[str],
    upload_base_dir: str,
    email_data_list: Optional[List[Dict]] = None
) -> Dict:
    """
    Process all documents for a specific user.
    
    Args:
        agent: DocumentIngestionAgent instance
        user_id: User identifier
        file_ids: List of file IDs to process
        upload_base_dir: Base upload directory
        email_data_list: Optional list of email data from Email Handling Agent
        
    Returns:
        Batch processing results ready for RFP Summary Agent
    """
    user_dir = Path(upload_base_dir) / f"user_{user_id}"
    
    # Build file list
    file_list = []
    for file_id in file_ids:
        # Load metadata
        metadata_file = user_dir / f"{file_id}_metadata.json"
        if metadata_file.exists():
            with open(metadata_file, 'r') as f:
                metadata = json.load(f)
            
            file_path = user_dir / metadata["saved_filename"]
            if file_path.exists():
                file_list.append({
                    "file_path": str(file_path),
                    "file_id": file_id,
                    "metadata": metadata
                })
    
    return agent.batch_ingest(file_list, email_data_list)


async def process_document_async(
    agent: DocumentIngestionAgent,
    file_path: str,
    file_id: str,
    metadata: Optional[Dict] = None
) -> Dict:
    """
    Async wrapper for document processing (useful for FastAPI integration).
    
    Args:
        agent: DocumentIngestionAgent instance
        file_path: Path to the file
        file_id: Unique file identifier
        metadata: Optional metadata dictionary
        
    Returns:
        Processing result dictionary
    """
    # The actual processing is CPU-bound, so we run it directly
    # In production, consider using asyncio.to_thread() for long operations
    return agent.ingest_document(file_path, file_id, metadata)
