# Transaction Processing System
> **📌 Assessment Submissions:**
> - 🎥 **3-Minute Technical Video Review:** [https://youtu.be/DJrE3hFw72c]
> - 📐 **System Architecture Diagram Photo:** [https://drive.google.com/file/d/1TfOyIBQGDmP4LzwWQn2N_v4jv93sVm84/view?usp=sharing]
> - 📏 **system Architecture Diagram Drawi.Io:** [https://viewer.diagrams.net/?tags=%7B%7D&lightbox=1&highlight=0000ff&edit=_blank&layers=1&nav=1&dark=auto#G1zJHblEjZG32gP08mbeceBITZP5afl44y]

## Over view

A backend system built with FastAPI to asynchronously process financial transaction CSV files. This system validates data using Pandas, detects anomalies, automatically classifies transaction categories using Google Gemini 1.5 Flash AI, and stores the results in a PostgreSQL database.

## Key Features
- **Asynchronous Processing**: Utilizes Celery and Redis to keep the API responsive while processing large files in the background.
- **Shared Volume**: Employs a Docker shared volume (`/tmp`) to isolate containers while allowing the API and Worker to seamlessly access the same uploaded files.
- **LLM Integration**: Classifies transaction categories using Google Gemini AI with a graceful error-handling mechanism to prevent system crashes during API rate limits.
- **Database Persistence**: Relational storage using PostgreSQL with dedicated tables for Jobs and Transactions.

---

## System Architecture & Data Flow

The system is designed with a decoupled architecture to handle large files efficiently without blocking the main API thread.

### A. Data Upload Flow (First Asynchronous Process)
1. **Client** sends a CSV file via HTTP POST request.
2. **FastAPI (Container API)** saves the physical file to the **Shared Volume (`/tmp`)**.
3. **FastAPI** creates a new job record in the **PostgreSQL Database** with the status set to `pending`.
4. **FastAPI** sends a queue message (containing the Job ID) to **Redis (Message Broker)**.
5. **FastAPI** immediately returns the `job_id` to the **Client** without waiting for the file to be processed.

### B. Background Processing Flow
1. **Celery (Container Worker)** collects the queue ticket from **Redis**.
2. **Celery Worker** updates the job status in **PostgreSQL** to `processing`.
3. **Celery Worker** reads the CSV file from the **Shared Volume (`/tmp`)** using **Pandas**.
4. **Pandas** cleans the data (handling missing values and converting `NaN` to `None`).
5. **Celery Worker** submits the transaction data to the **Google Gemini AI API** for classification.
6. **Gemini AI API** returns the classification category text back to the **Celery Worker**.
7. **Celery Worker** evaluates the logic rules to determine if a transaction `is_anomaly`.
8. **Celery Worker** saves the fully cleaned and categorized data into the `transactions` table in **PostgreSQL**.
9. **Celery Worker** updates the final job status in **PostgreSQL** to `completed` (or `failed` if an error occurs).

### C. Process for Users to Retrieve Results
1. **Client** sends an HTTP GET request with the `job_id` to the `/status` or `/results` endpoint.
2. **FastAPI** executes a read query to the **PostgreSQL Database**.
3. **PostgreSQL** returns the requested data (status or transaction rows).
4. **FastAPI** compiles the data into a concise JSON format and sends it back to the **Client**.

---

## Prerequisites & Setup

1. Ensure **Docker** and **Docker Compose** are installed on your machine.
2. Create a `.env` file in the root directory of the project and insert your Gemini API Key:
   ```env
   GEMINI_API_KEY=apikeyinhere

## HOW TO RUN THE SYSTEM
**follow the step**
1. docker compose up --build
2. Access to this link in browser http://localhost:8000/docs

## Example cURL Requests
1. Upload Transactions (Start a Job)

curl -X 'POST' \
  'http://localhost:8000/jobs/upload' \
  -H 'accept: application/json' \
  -H 'Content-Type: multipart/form-data' \
  -F 'file=@transactions.csv;type=text/csv'

**Example Respon**

{
  "job_id": 1,
  "message": "File uploaded and processing started."
}

2. Get Job Status

curl -X 'GET' \
  'http://localhost:8000/jobs/1/status' \
  -H 'accept: application/json'

**Example Response:**

{
  "job_id": 1,
  "status": "completed",
  "filename": "transactions.csv",
  "row_count_raw": 85,
  "row_count_clean": 85,
  "created_at": "2026-06-25T08:17:31.248519",
  "summary": "Total spending is high on Shopping..."
}

3. Get Job Results

curl -X 'GET' \
  'http://localhost:8000/jobs/1/results' \
  -H 'accept: application/json'

**Example Response:**

{
  "job_id": 1,
  "transactions": [
    {
      "txn_id": "TXN1065",
      "date": "2024-09-04",
      "merchant": "Flipkart",
      "amount": 10882.55,
      "currency": "INR",
      "status": "SUCCESS",
      "category": "Shopping",
      "is_anomaly": false,
      "anomaly_reason": ""
    }
  ]
}

