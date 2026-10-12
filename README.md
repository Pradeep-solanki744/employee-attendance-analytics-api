# Employee Attendance & Analytics API

A REST API built with FastAPI and MongoDB for employee management, attendance tracking, and attendance analytics.

## Requirements

* Python 3.11 or newer
* MongoDB 6.0 or newer

## Setup

Install the project dependencies:

```bash
pip install -r requirements.txt
```

Configure the following environment variables:

* `MONGO_URI`: MongoDB connection string
* `MONGO_DB`: MongoDB database name

For local development, these variables can be stored in a local `.env` file. Do not commit `.env` or any credentials to Git.

Example local configuration:

```env
MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db
```

Make sure MongoDB is running before starting the application.

## Run the API

From the repository root, run:

```bash
uvicorn app.main:app --port 8000
```

The API will be available at `http://127.0.0.1:8000`.

## Health Check

Send a GET request to:

```text
/health
```

The endpoint returns HTTP 200 when the application can reach MongoDB.

## API Documentation

When the application is running, open these paths in your browser:

* `/docs` — interactive Swagger API documentation
* `/openapi.json` — generated OpenAPI schema

## Database Setup

The application creates its required indexes at startup. If you want to load the supplied sample data, review `sample_seed.py` before running it against your configured database.

## Project Files

* `app/main.py` — API implementation
* `requirements.txt` — Python dependencies
* `REVIEW.md` — review of starter-code defects
* `DECISIONS.md` — implementation decisions and trade-offs
* `openapi.yaml` — assignment API contract
* `DATA_MODEL.md` — database model reference
