"""
File Upload Routes - Handle RFP document uploads
Users can upload RFP documents manually (PDFs, DOCx, XLSX, etc.)
"""
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Form
from fastapi.responses import JSONResponse, FileResponse
from typing import List, Optional, Dict
import os
import shutil
from pathlib import Path
from datetime import datetime
import uuid
import mimetypes

from finops_stack.api.schemas import (
    FileUploadResponse,
    FileListResponse,
    FileInfoResponse
)
from finops_stack.iam.auth_guard import get_user_with_roles


router = APIRouter()



# ============================================================================
# Configuration
# ============================================================================

UPLOAD_BASE_DIR = Path("./rfp_uploads")
UPLOAD_BASE_DIR.mkdir(parents=True, exist_ok=True)

# Allowed file types for RFP documents
ALLOWED_EXTENSIONS = {
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', 
    '.txt', '.csv', '.zip', '.rar', '.7z',
    '.ppt', '.pptx', '.jpg', '.jpeg', '.png'
}

# Maximum file size (50MB)
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB in bytes


# ============================================================================
# Helper Functions
# ============================================================================

def get_user_upload_dir(user_id: str) -> Path:
    """Get or create user's upload directory"""
    user_dir = UPLOAD_BASE_DIR / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


def sanitize_filename(filename: str) -> str:
    """Sanitize filename to prevent directory traversal attacks"""
    # Remove any path components
    filename = os.path.basename(filename)
    
    # Replace unsafe characters
    unsafe_chars = '<>:"/\\|?*'
    for char in unsafe_chars:
        filename = filename.replace(char, '_')
    
    # Limit length
    max_length = 200
    if len(filename) > max_length:
        name, ext = os.path.splitext(filename)
        filename = name[:max_length - len(ext)] + ext
    
    return filename


def validate_file_extension(filename: str) -> bool:
    """Check if file extension is allowed"""
    ext = os.path.splitext(filename)[1].lower()
    return ext in ALLOWED_EXTENSIONS


def get_file_metadata(file_path: Path) -> Dict:
    """Extract file metadata"""
    stat = file_path.stat()
    mime_type, _ = mimetypes.guess_type(str(file_path))
    
    return {
        "filename": file_path.name,
        "size_bytes": stat.st_size,
        "size_kb": round(stat.st_size / 1024, 2),
        "size_mb": round(stat.st_size / (1024 * 1024), 2),
        "mime_type": mime_type or "application/octet-stream",
        "extension": file_path.suffix,
        "created_at": datetime.fromtimestamp(stat.st_ctime).isoformat(),
        "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "file_path": str(file_path.absolute())
    }


# ============================================================================
# File Upload Endpoints
# ============================================================================

@router.post("/upload", response_model=FileUploadResponse)
async def upload_rfp_file(
    file: UploadFile = File(...),
    rfp_name: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    category: Optional[str] = Form(None),
    user_data: Dict = Depends(get_user_with_roles)
):
    """
    Upload a single RFP document
    
    **Authentication:** Requires valid JWT token
    
    **Form Data:**
    - file: File to upload (PDF, DOCx, XLSX, etc.)
    - rfp_name: Optional RFP name/title
    - description: Optional description
    - category: Optional category (e.g., "proposal", "requirement", "budget")
    
    **Allowed File Types:**
    - Documents: PDF, DOC, DOCX, TXT
    - Spreadsheets: XLS, XLSX, CSV
    - Presentations: PPT, PPTX
    - Archives: ZIP, RAR, 7Z
    - Images: JPG, JPEG, PNG
    
    **Max File Size:** 50MB
    
    **Response:**
    - success: bool
    - file_id: str (unique identifier)
    - filename: str
    - size_mb: float
    - upload_path: str
    - message: str
    """
    authenticated_user_id = user_data["user_id"]
    
    # Validate file extension
    if not validate_file_extension(file.filename):
        raise HTTPException(
            status_code=400,
            detail=f"File type not allowed. Allowed types: {', '.join(ALLOWED_EXTENSIONS)}"
        )
    
    # Read file content
    try:
        contents = await file.read()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read file: {str(e)}")
    
    # Validate file size
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {MAX_FILE_SIZE / (1024*1024):.0f}MB"
        )
    
    # Generate unique file ID
    file_id = str(uuid.uuid4())
    
    # Sanitize filename
    safe_filename = sanitize_filename(file.filename)
    
    # Create unique filename with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    unique_filename = f"{timestamp}_{file_id[:8]}_{safe_filename}"
    
    # Get user's upload directory
    user_dir = get_user_upload_dir(authenticated_user_id)
    
    # Save file
    file_path = user_dir / unique_filename
    try:
        with open(file_path, "wb") as f:
            f.write(contents)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {str(e)}")
    
    # Get file metadata
    metadata = get_file_metadata(file_path)
    
    # Save metadata to JSON (optional - for tracking)
    metadata_file = user_dir / f"{file_id}_metadata.json"
    import json
    with open(metadata_file, "w") as f:
        json.dump({
            "file_id": file_id,
            "original_filename": file.filename,
            "saved_filename": unique_filename,
            "rfp_name": rfp_name,
            "description": description,
            "category": category,
            "uploaded_by": authenticated_user_id,
            "uploaded_at": datetime.now().isoformat(),
            **metadata
        }, f, indent=2)
    
    return FileUploadResponse(
        success=True,
        file_id=file_id,
        filename=unique_filename,
        original_filename=file.filename,
        size_mb=metadata["size_mb"],
        upload_path=str(file_path.relative_to(UPLOAD_BASE_DIR)),
        mime_type=metadata["mime_type"],
        message="File uploaded successfully"
    )


@router.post("/upload-multiple", response_model=Dict)
async def upload_multiple_rfp_files(
    files: List[UploadFile] = File(...),
    rfp_name: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    category: Optional[str] = Form(None),
    user_data: Dict = Depends(get_user_with_roles)
):
    """
    Upload multiple RFP documents at once
    
    **Authentication:** Requires valid JWT token
    
    **Form Data:**
    - files: Multiple files to upload
    - rfp_name: Optional RFP name for all files
    - description: Optional description for all files
    
    **Response:**
    - success: bool
    - total_files: int
    - uploaded_files: List[Dict] (details of each uploaded file)
    - failed_files: List[Dict] (files that failed to upload)
    """
    authenticated_user_id = user_data["user_id"]
    
    uploaded_files = []
    failed_files = []
    
    for file in files:
        try:
            # Validate file extension
            if not validate_file_extension(file.filename):
                failed_files.append({
                    "filename": file.filename,
                    "error": "File type not allowed"
                })
                continue
            
            # Read file content
            contents = await file.read()
            
            # Validate file size
            if len(contents) > MAX_FILE_SIZE:
                failed_files.append({
                    "filename": file.filename,
                    "error": f"File too large (max {MAX_FILE_SIZE / (1024*1024):.0f}MB)"
                })
                continue
            
            # Generate unique file ID
            file_id = str(uuid.uuid4())
            
            # Sanitize filename
            safe_filename = sanitize_filename(file.filename)
            
            # Create unique filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            unique_filename = f"{timestamp}_{file_id[:8]}_{safe_filename}"
            
            # Get user's upload directory
            user_dir = get_user_upload_dir(authenticated_user_id)
            
            # Save file
            file_path = user_dir / unique_filename
            with open(file_path, "wb") as f:
                f.write(contents)

            metadata = get_file_metadata(file_path)
            
            # Get file metadata
            metadata_file = user_dir / f"{file_id}_metadata.json"
            import json
            with open(metadata_file, "w") as f:
                json.dump({
                    "file_id": file_id,
                    "original_filename": file.filename,
                    "saved_filename": unique_filename,
                    "rfp_name": rfp_name,
                    "description": description,
                    "category": category,
                    "uploaded_by": authenticated_user_id,
                    "uploaded_at": datetime.now().isoformat(),
                    **metadata
                }, f, indent=2)


            uploaded_files.append({
                "file_id": file_id,
                "filename": unique_filename,
                "original_filename": file.filename,
                "size_mb": metadata["size_mb"],
                "upload_path": str(file_path.relative_to(UPLOAD_BASE_DIR)),
                "mime_type": metadata.get("mime_type")
            })
            
        except Exception as e:
            failed_files.append({
                "filename": file.filename,
                "error": str(e)
            })
    
    return {
        "success": len(uploaded_files) > 0,
        "total_files": len(files),
        "uploaded_count": len(uploaded_files),
        "failed_count": len(failed_files),
        "uploaded_files": uploaded_files,
        "failed_files": failed_files,
        "message": f"Uploaded {len(uploaded_files)}/{len(files)} files successfully"
    }


# ============================================================================
# File Management Endpoints
# ============================================================================


@router.get("/list", response_model=FileListResponse)
async def list_user_files(
    category: Optional[str] = None,
    user_data: Dict = Depends(get_user_with_roles)
):
    """
    List all uploaded files for authenticated user
    
    **Authentication:** Requires valid JWT token
    
    **Query Parameters:**
    - category: Optional filter by category
    
    **Response:**
    - success: bool
    - total_files: int
    - files: List of file metadata
    """
    authenticated_user_id = user_data["user_id"]
    
    user_dir = get_user_upload_dir(authenticated_user_id)
    
    files = []
    
    # List all files (excluding metadata JSON files)
    for file_path in user_dir.glob("*"):
        if file_path.suffix == ".json" or file_path.name.startswith("."):
            continue
        
        # Get metadata
        metadata = get_file_metadata(file_path)
        
        # Try to load additional metadata from JSON
        file_id = None
        rfp_name = None
        description = None
        file_category = None
        
        # Look for corresponding metadata file
        for json_file in user_dir.glob("*_metadata.json"):
            try:
                import json
                with open(json_file, 'r') as f:
                    meta = json.load(f)
                    if meta.get("saved_filename") == file_path.name:
                        file_id = meta.get("file_id")
                        rfp_name = meta.get("rfp_name")
                        description = meta.get("description")
                        file_category = meta.get("category")
                        break
            except:
                continue
        
        # Apply category filter
        if category and file_category != category:
            continue
        
        files.append({
            "file_id": file_id,
            "filename": file_path.name,
            "rfp_name": rfp_name,
            "description": description,
            "category": file_category,
            **metadata
        })
    
    # Sort by modified date (newest first)
    files.sort(key=lambda x: x["modified_at"], reverse=True)
    
    return FileListResponse(
        success=True,
        total_files=len(files),
        files=files
    )


@router.get("/download/{file_id}")
async def download_file(
    file_id: str,
    user_data: Dict = Depends(get_user_with_roles)
):
    """
    Download a previously uploaded file
    
    **Authentication:** Requires valid JWT token
    
    **Path Parameters:**
    - file_id: Unique file identifier
    
    **Response:** Binary file download
    """
    authenticated_user_id = user_data["user_id"]
    
    user_dir = get_user_upload_dir(authenticated_user_id)
    
    # Find file by file_id
    metadata_file = user_dir / f"{file_id}_metadata.json"
    
    if not metadata_file.exists():
        raise HTTPException(status_code=404, detail="File not found")
    
    # Load metadata
    import json
    with open(metadata_file, 'r') as f:
        metadata = json.load(f)
    
    saved_filename = metadata.get("saved_filename")
    original_filename = metadata.get("original_filename")
    
    file_path = user_dir / saved_filename
    
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")
    
    return FileResponse(
        path=file_path,
        filename=original_filename,  # Use original filename for download
        media_type=metadata.get("mime_type", "application/octet-stream")
    )


@router.get("/info/{file_id}", response_model=FileInfoResponse)
async def get_file_info(
    file_id: str,
    user_data: Dict = Depends(get_user_with_roles)
):
    """
    Get detailed information about a file
    
    **Authentication:** Requires valid JWT token
    
    **Path Parameters:**
    - file_id: Unique file identifier
    
    **Response:** Detailed file metadata
    """
    authenticated_user_id = user_data["user_id"]
    
    user_dir = get_user_upload_dir(authenticated_user_id)
    
    # Find file by file_id
    metadata_file = user_dir / f"{file_id}_metadata.json"
    
    if not metadata_file.exists():
        raise HTTPException(status_code=404, detail="File not found")
    
    # Load metadata
    import json
    with open(metadata_file, 'r') as f:
        metadata = json.load(f)
    
    return FileInfoResponse(
        success=True,
        file_info=metadata
    )


@router.delete("/delete/{file_id}")
async def delete_file(
    file_id: str,
    user_data: Dict = Depends(get_user_with_roles)
):
    """
    Delete an uploaded file
    
    **Authentication:** Requires valid JWT token
    
    **Path Parameters:**
    - file_id: Unique file identifier
    
    **Response:**
    - success: bool
    - message: str
    """
    authenticated_user_id = user_data["user_id"]
    
    user_dir = get_user_upload_dir(authenticated_user_id)
    
    # Find file by file_id
    metadata_file = user_dir / f"{file_id}_metadata.json"
    
    if not metadata_file.exists():
        raise HTTPException(status_code=404, detail="File not found")
    
    # Load metadata
    import json
    with open(metadata_file, 'r') as f:
        metadata = json.load(f)
    
    saved_filename = metadata.get("saved_filename")
    file_path = user_dir / saved_filename
    
    # Delete file and metadata
    try:
        if file_path.exists():
            file_path.unlink()
        metadata_file.unlink()
        
        return {
            "success": True,
            "message": "File deleted successfully",
            "file_id": file_id
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete file: {str(e)}")


@router.delete("/delete-all")
async def delete_all_files(
    confirm: bool = False,
    user_data: Dict = Depends(get_user_with_roles)
):
    """
    Delete all uploaded files for authenticated user
    
    **Authentication:** Requires valid JWT token
    
    **Query Parameters:**
    - confirm: Must be true to actually delete (safety check)
    
    **Response:**
    - success: bool
    - deleted_count: int
    - message: str
    """
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Must set confirm=true to delete all files"
        )
    
    authenticated_user_id = user_data["user_id"]
    
    user_dir = get_user_upload_dir(authenticated_user_id)
    
    deleted_count = 0
    
    # Delete all files and metadata
    for file_path in user_dir.glob("*"):
        try:
            file_path.unlink()
            deleted_count += 1
        except Exception as e:
            print(f"Failed to delete {file_path}: {e}")
    
    return {
        "success": True,
        "deleted_count": deleted_count,
        "message": f"Deleted {deleted_count} files"
    }


# ============================================================================
# Storage Statistics
# ============================================================================

@router.get("/storage-stats")
async def get_storage_stats(user_data: Dict = Depends(get_user_with_roles)):
    """
    Get storage statistics for authenticated user
    
    **Authentication:** Requires valid JWT token
    
    **Response:**
    - user_id: str
    - total_files: int
    - total_size_mb: float
    - files_by_type: Dict (count by file extension)
    """
    authenticated_user_id = user_data["user_id"]
    
    user_dir = get_user_upload_dir(authenticated_user_id)
    
    total_files = 0
    total_size = 0
    files_by_type = {}
    
    for file_path in user_dir.glob("*"):
        if file_path.suffix == ".json" or file_path.name.startswith("."):
            continue
        
        total_files += 1
        total_size += file_path.stat().st_size
        
        ext = file_path.suffix.lower()
        files_by_type[ext] = files_by_type.get(ext, 0) + 1
    
    return {
        "user_id": authenticated_user_id,
        "total_files": total_files,
        "total_size_bytes": total_size,
        "total_size_mb": round(total_size / (1024 * 1024), 2),
        "files_by_type": files_by_type,
        "upload_directory": str(user_dir.relative_to(UPLOAD_BASE_DIR))
    }
