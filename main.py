from fastapi import FastAPI,HTTPException,UploadFile,File
from database import db
from fastapi.middleware.cors import CORSMiddleware
from bson.errors import InvalidId
from bson import ObjectId
from pydantic import BaseModel
from typing import List, Optional
import httpx
import os
from dotenv import load_dotenv
from PyPDF2 import PdfReader
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2 import service_account
import tempfile
import shutil


load_dotenv()

app = FastAPI()


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",  # React dev
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



# =========================
# OPENAI CONFIG
# =========================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_API_URL   = os.getenv("OPENAI_API_URL")
SCOPES = os.getenv("SCOPES")
SERVICE_ACCOUNT_FILE =os.getenv("SERVICE_ACCOUNT_FILE")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY not set")

# =========================
# MODELS
# =========================
class Message(BaseModel):
    role: str  # system | user | assistant
    content: str

class OpenAIRequest(BaseModel):
    model: str = "gpt-4o"
    max_tokens: int = 1000
    messages: List[Message]

class Question(BaseModel):
    questionText: str

class JobResponse(BaseModel):
    id: str
    title: str
    description: str
    questions: List[Question]
    numberOfQuestions: int




@app.post("/api/openai")
async def openai_proxy(request: OpenAIRequest):
    """
    Secure proxy endpoint for OpenAI Chat Completions
    """
    try:
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": request.model,
            "messages": [
                {"role": msg.role, "content": msg.content}
                for msg in request.messages
            ],
            "max_tokens": request.max_tokens,
            "temperature": 0.7
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                OPENAI_API_URL,
                headers=headers,
                json=payload
            )

        if response.status_code != 200:
            raise HTTPException(
                status_code=response.status_code,
                detail=f"OpenAI API error: {response.text}"
            )

        return response.json()

    except httpx.RequestError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to connect to OpenAI API: {str(e)}"
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )


@app.get("/")
async def root():
    print("ENV_CHECK:", os.getenv("ENV_CHECK"))
    print("opena ai key",OPENAI_API_KEY)
    print("open ai url",OPENAI_API_URL)
    print("scopes",SCOPES,"SErvice acount file",SERVICE_ACCOUNT_FILE,"Drive folder id",DRIVE_FOLDER_ID)
    return {"status": "MongoDB connected successfully"}




def convert_objectid(data):
    if isinstance(data, list):
        return [convert_objectid(item) for item in data]
    elif isinstance(data, dict):
        return {key: convert_objectid(value) for key, value in data.items()}
    elif isinstance(data, ObjectId):
        return str(data)
    else:
        return data

@app.get("/jobs/{_id}")
async def get_job_details(_id: str):
    try:
        object_id = ObjectId(_id)
    except InvalidId:
        raise HTTPException(status_code=400, detail="Invalid job ID")

    job = await db.jobs.find_one({"_id": object_id})

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    job = convert_objectid(job)  # 🔥 THIS FIXES EVERYTHING

    return {
        "job_id": job["_id"],
        "title": job.get("jobTitle"),
        "description": job.get("plainTextJobDescription"),
        "questions": job.get("questions", [])
    }

creds = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES
)

drive_service = build("drive", "v3", credentials=creds)

# -------------------- DRIVE UPLOAD FUNCTION --------------------
def upload_to_drive(file_path: str, filename: str):
    file_metadata = {
        "name": filename,
        "parents": [DRIVE_FOLDER_ID]
    }

    media = MediaFileUpload(file_path, mimetype="application/pdf")

    file = drive_service.files().create(
    body={
        "name": filename,
        "parents": [DRIVE_FOLDER_ID]
    },
    media_body=media,
    fields="id, webViewLink",
    supportsAllDrives=True
).execute()

    return file["webViewLink"]

# -------------------- API ENDPOINT --------------------
@app.post("/jobs/upload-resume")
async def upload_pdf(resume: UploadFile = File(...)):
    # 1️⃣ Validate content type
    if resume.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Only PDF files allowed")

    # 2️⃣ Save temporarily
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        shutil.copyfileobj(resume.file, tmp)
        tmp_path = tmp.name

    try:
        # 3️⃣ Validate page count
        reader = PdfReader(tmp_path)
        page_count = len(reader.pages)

        if page_count > 3:
            raise HTTPException(
                status_code=400,
                detail="PDF must contain 2 or 3 pages only"
            )

        # 4️⃣ Upload to Google Drive
        drive_url = upload_to_drive(tmp_path, resume.filename)

        # 5️⃣ Save to DB (example)
        # db.save({
        #   "file_name": file.filename,
        #   "page_count": page_count,
        #   "drive_url": drive_url
        # })

        return {
            "message": "Upload successful",
            "url": drive_url,
            "page_count": page_count
        }

    finally:
        # 6️⃣ Cleanup temp file
        os.remove(tmp_path)