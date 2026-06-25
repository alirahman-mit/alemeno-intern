from fastapi import FastAPI, UploadFile, File, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os
import shutil

from app.models import Job, Transaction, JobSummary, Base
from app.schemas import JobResponse, JobStatusResponse
from app.worker import process_csv_job

# Setup Database
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@db:5432/alemeno_db")
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Alemeno Transaction Processing API")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/jobs/upload", response_model=JobResponse)
async def upload_transactions(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename.endswith('.csv'):
     raise HTTPException(status_code=400, detail="Must be a CSV file")

    # Simpan file ke server
    os.makedirs("/tmp", exist_ok=True)
    file_location = f"/tmp/{file.filename}"
    with open(file_location, "wb+") as file_object:
        shutil.copyfileobj(file.file, file_object)

    # Catat job di database
    new_job = Job(filename=file.filename, status="pending")
    db.add(new_job)
    db.commit()
    db.refresh(new_job)

    # Kirim instruksi ke Celery Worker
    process_csv_job.delay(new_job.id, file_location)

    return {"job_id": new_job.id, "message": "File uploaded and processing started."}

@app.get("/jobs/{job_id}/status", response_model=JobStatusResponse)
def get_job_status(job_id: int, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    summary_data = None
    if job.status == "completed" and job.summary:
        summary_data = {
            "total_spend_inr": job.summary.total_spend_inr,
            "total_spend_usd": job.summary.total_spend_usd,
            "top_merchants": job.summary.top_merchants,
            "anomaly_count": job.summary.anomaly_count,
            "narrative": job.summary.narrative,
            "risk_level": job.summary.risk_level
        }

    return {
        "job_id": job.id,
        "status": job.status,
        "filename": job.filename,
        "row_count_raw": job.row_count_raw,
        "row_count_clean": job.row_count_clean,
        "created_at": job.created_at,
        "summary": summary_data
    }

@app.get("/jobs/{job_id}/results")
def get_job_results(job_id: int, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "completed":
        raise HTTPException(status_code=400, detail=f"Job is currently: {job.status}")

    transactions = db.query(Transaction).filter(Transaction.job_id == job_id).all()
    
    return {
        "job_id": job.id,
        "transactions": [
            {
                "txn_id": t.txn_id, "date": t.date, "merchant": t.merchant,
                "amount": t.amount, "currency": t.currency, "status": t.status,
                "category": t.category, "is_anomaly": t.is_anomaly,
                "anomaly_reason": t.anomaly_reason
            } for t in transactions
        ]
    }

@app.get("/jobs")
def list_jobs(status: str = None, db: Session = Depends(get_db)):
    query = db.query(Job)
    if status:
        query = query.filter(Job.status == status)
    
    jobs = query.all()
    return [{"job_id": j.id, "filename": j.filename, "status": j.status, "created_at": j.created_at} for j in jobs]