import json
import os
import pandas as pd
from google import genai
from celery import Celery
from google.genai import types
from tenacity import retry, stop_after_attempt, wait_exponential
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from datetime import datetime
from app.models import Job, Transaction, JobSummary

#setup database
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@db:5432/alemeno_db")
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
db = SessionLocal()

redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0") 
celery_app = Celery("worker", broker=redis_url, backend=redis_url)

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
#function LLM 
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def categorize_with_gemini(batch_data):
    """Fungsi klasifikasi transaksi dengan retry logic """
    prompt = f"""
    Kategorikan transaksi berikut ke dalam: Food, Shopping, Travel, Transport, Utilities, Cash Withdrawal, Entertainment, atau Other[cite: 49].
    Kembalikan HANYA array JSON berisi object dengan key 'txn_id' dan 'category'.
    Data: {json.dumps(batch_data)}
    """
    response = client.models.generate_content(
        model='gemini-1.5-flash',
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json")
    )
    return json.loads(response.text)

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def generate_narrative_summary(df_clean):
    """Fungsi untuk membuat summary JSON akhir """
    summary_data = df_clean[['merchant', 'amount', 'currency', 'is_anomaly']].to_dict(orient='records')
    prompt = f"""
    Buat ringkasan data pengeluaran berikut dalam format JSON dengan key:
    - total_spend_inr (angka)
    - total_spend_usd (angka)
    - top_3_merchants (array nama merchant)
    - anomaly_count (angka)
    - narrative (2-3 kalimat narasi pengeluaran)
    - risk_level (low/medium/high)
    Data: {json.dumps(summary_data)}
    """
    response = client.models.generate_content(
        model='gemini-1.5-flash',
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json")
    )
    return json.loads(response.text)

@celery_app.task(bind=True)
def process_csv_job(self, job_id, csv_path):
    """Ini adalah task yang dipanggil oleh API FastAPI saat file diupload"""
    try:
        # --- Data Cleaning & Anomaly Detection ---
        df = pd.read_csv(csv_path)
        
        # Cleaning [cite: 43, 44]
        df['amount'] = df['amount'].astype(str).str.replace('$', '', regex=False).astype(float)
        df['date'] = pd.to_datetime(df['date'], format='mixed', dayfirst=True).dt.strftime('%Y-%m-%d')
        df['currency'] = df['currency'].str.upper()
        df['status'] = df['status'].str.upper()
        df['category'] = df['category'].fillna('Uncategorised')
        df = df.drop_duplicates()

        # Anomaly [cite: 46, 47]
        df['is_anomaly'] = False
        df['anomaly_reason'] = ""
        
        medians = df.groupby('account_id')['amount'].transform('median')
        outlier_mask = df['amount'] > (3 * medians)
        df.loc[outlier_mask, 'is_anomaly'] = True
        df.loc[outlier_mask, 'anomaly_reason'] += "Amount > 3x median. "

        domestic_merchants = ['SWIGGY', 'OLA', 'IRCTC']
        usd_domestic_mask = (df['currency'] == 'USD') & (df['merchant'].str.upper().isin(domestic_merchants))
        df.loc[usd_domestic_mask, 'is_anomaly'] = True
        df.loc[usd_domestic_mask, 'anomaly_reason'] += "USD for domestic merchant."

        # --- LLM Classification ---
        df['llm_failed'] = False
        uncategorised_df = df[df['category'] == 'Uncategorised']
        
        if not uncategorised_df.empty:
            batch_data = uncategorised_df[['txn_id', 'merchant', 'amount', 'notes']].to_dict(orient='records')
            try:
                llm_results = categorize_with_gemini(batch_data)
                for item in llm_results:
                    df.loc[df['txn_id'] == item['txn_id'], 'category'] = item['category']
            except Exception as e:
                print(f"LLM Klasifikasi gagal: {e}")
                df.loc[df['category'] == 'Uncategorised', 'llm_failed'] = True

        try:
            summary_json = generate_narrative_summary(df)
        except Exception as e:
            print(f"LLM Summary gagal: {e}")
            summary_json = {}
        
        try:
            # 1. Update status Job
            job_record = db.query(Job).filter(Job.id == job_id).first()
            if job_record:
                job_record.status = "completed"
                job_record.row_count_clean = len(df)
                job_record.completed_at = datetime.utcnow()

            # 2. Persiapkan data transaksi (konversi dataframe ke list of dictionaries)
            # Pastikan menempelkan job_id ke setiap transaksi
            df['job_id'] = job_id
            
            # Kita gunakan orient='records' lalu mapping langsung ke model SQLAlchemy
            df = df.astype(object).where(pd.notna(df), None)
            df_records = df.to_dict(orient='records')
            
            # Bulk insert transaksi agar lebih cepat daripada insert satu per satu
            transactions_to_insert = [Transaction(**row) for row in df_records]
            db.add_all(transactions_to_insert)

            # 3. Simpan Job Summary (if LLM succes make it)
            if summary_json:
                summary_record = JobSummary(
                    job_id=job_id,
                    total_spend_inr=summary_json.get("total_spend_inr", 0.0),
                    total_spend_usd=summary_json.get("total_spend_usd", 0.0),
                    top_merchants=summary_json.get("top_3_merchants", []),
                    anomaly_count=summary_json.get("anomaly_count", 0),
                    narrative=summary_json.get("narrative", ""),
                    risk_level=summary_json.get("risk_level", "medium")
                )
                db.add(summary_record)

            #  Commit all change to database together
            db.commit()

            return {"status": "completed", "job_id": job_id}

        except Exception as e:
            db.rollback() # Jika ada error saat save, batalkan semua agar tidak ada data setengah jadi
            print(f"Gagal menyimpan ke database: {e}")
            
            if job_record:
                job_record.status = "failed"
                job_record.error_message = f"Database error: {str(e)}"
                db.commit()
                
            return {"status": "failed", "error": str(e)}
        finally:
            db.close()
        return {"status": "completed", "job_id": job_id}
    
    except Exception as e:
        # update status 'failed' di tabel Job
        print(f"Job {job_id} gagal diproses: {e}")
        return {"status": "failed", "error": str(e)}