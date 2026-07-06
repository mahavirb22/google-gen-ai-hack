#!/usr/bin/env python3
"""
Orchestration script to:
1. Load credentials from .env.
2. Generate synthetic data (if not already present).
3. Run feature engineering (GPU risk scoring) locally to produce transactions_processed.csv.
4. Create the GCS bucket and BigQuery dataset in Google Cloud.
5. Upload the processed CSV to GCS.
6. Trigger the BigQuery load job to ingest the data with the expanded schema.
"""

import os
import sys
import time
import logging
import pandas as pd
from google.cloud import bigquery
from google.cloud import storage
from google.api_core.exceptions import Conflict, GoogleAPIError

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("deploy_data")

def load_env():
    """Reads key-value pairs from .env file into os.environ."""
    env_path = ".env"
    if os.path.exists(env_path):
        logger.info("Loading environment configurations from %s...", env_path)
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    # Strip quotes if present
                    val = val.strip().strip('"').strip("'")
                    os.environ[key.strip()] = val
    else:
        logger.warning(".env file not found, using existing environment variables.")

def run_deployment():
    # 1. Load env variables
    load_env()
    
    project_id = os.getenv("GCP_PROJECT_ID")
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    dataset_id = os.getenv("BQ_DATASET_ID", "delinquency_engine")
    table_id = os.getenv("BQ_TABLE_ID", "transactions")
    region = os.getenv("GCP_REGION", "us-central1")
    
    if not project_id:
        logger.error("GCP_PROJECT_ID environment variable is missing!")
        sys.exit(1)
        
    if credentials_path:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_path
        logger.info("Set GOOGLE_APPLICATION_CREDENTIALS to: %s", credentials_path)

    bucket_name = f"{project_id}-delinquency-raw-data"
    raw_csv_filename = "transactions_raw.csv"
    processed_csv_filename = "transactions_processed.csv"
    gcs_uri = f"gs://{bucket_name}/{processed_csv_filename}"

    # Add scripts directory to path to ensure imports resolve
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    # 2. Generate Synthetic Data if not already exists
    if not os.path.exists(raw_csv_filename):
        logger.info("%s not found. Initiating synthetic generation...", raw_csv_filename)
        from generate_synthetic_data import generate_data
        size = 100000
        logger.info("Generating %d rows of transaction records...", size)
        generate_data(size=size, output_path=raw_csv_filename)
    else:
        logger.info("Found existing local %s. Skipping generation.", raw_csv_filename)

    # 3. Compute Risk Scores using GPU feature engineering pipeline
    if not os.path.exists(processed_csv_filename):
        logger.info("Running feature engineering pipeline (gpu_risk_scoring) to compute delinquency risk scores...")
        from gpu_risk_scoring import run_feature_engineering
        df_raw = pd.read_csv(raw_csv_filename)
        df_processed = run_feature_engineering(df_raw)
        df_processed.to_csv(processed_csv_filename, index=False)
        logger.info("Successfully generated processed transactions: %s", processed_csv_filename)
    else:
        logger.info("Found existing local %s. Skipping feature engineering.", processed_csv_filename)

    # Initialize client instances
    try:
        storage_client = storage.Client(project=project_id)
        bq_client = bigquery.Client(project=project_id)
    except Exception as e:
        logger.error("Failed to initialize Google Cloud clients: %s", e)
        logger.error("Please verify that your Service Account JSON key is correct and accessible.")
        sys.exit(1)

    # 4. Create GCS Bucket
    logger.info("Ensuring GCS bucket gs://%s exists...", bucket_name)
    try:
        bucket = storage_client.bucket(bucket_name)
        if not bucket.exists():
            bucket.storage_class = "STANDARD"
            storage_client.create_bucket(bucket, location=region)
            logger.info("Successfully created GCS bucket: %s in %s", bucket_name, region)
        else:
            logger.info("GCS bucket %s already exists.", bucket_name)
    except Conflict:
        logger.info("GCS bucket %s exists but owned by another project or conflict. Skipping.", bucket_name)
    except GoogleAPIError as e:
        logger.error("Failed to check/create GCS bucket: %s", e)
        sys.exit(1)

    # 5. Upload processed CSV to GCS
    logger.info("Uploading %s to GCS bucket...", processed_csv_filename)
    try:
        blob = storage_client.bucket(bucket_name).blob(processed_csv_filename)
        blob.upload_from_filename(processed_csv_filename)
        logger.info("Successfully uploaded %s to GCS.", processed_csv_filename)
    except GoogleAPIError as e:
        logger.error("Failed to upload file to storage bucket: %s", e)
        sys.exit(1)

    # 6. Create BigQuery Dataset
    dataset_ref = f"{project_id}.{dataset_id}"
    logger.info("Ensuring BigQuery dataset %s exists...", dataset_ref)
    try:
        dataset = bigquery.Dataset(dataset_ref)
        dataset.location = region
        dataset.description = "Dataset for the Pre-Delinquency Intervention Engine"
        bq_client.create_dataset(dataset, exists_ok=True)
        logger.info("Successfully verified/created BigQuery dataset: %s", dataset_id)
    except GoogleAPIError as e:
        logger.error("Failed to check/create BigQuery dataset: %s", e)
        sys.exit(1)

    # 7. Load Processed CSV from GCS to BigQuery Table
    table_ref = f"{project_id}.{dataset_id}.{table_id}"
    logger.info("Submitting BigQuery Load Job for table: %s", table_ref)

    # Schema definition including feature-engineered risk columns
    schema = [
        bigquery.SchemaField("user_id", "STRING", mode="REQUIRED", description="Unique identifier for the customer/user"),
        bigquery.SchemaField("timestamp", "TIMESTAMP", mode="REQUIRED", description="The date and time when the transaction occurred"),
        bigquery.SchemaField("transaction_amount", "NUMERIC", mode="REQUIRED", description="The amount of the transaction"),
        bigquery.SchemaField("merchant_category", "STRING", mode="NULLABLE", description="The industry category of the merchant"),
        bigquery.SchemaField("account_balance", "NUMERIC", mode="REQUIRED", description="The user's account balance immediately after the transaction"),
        bigquery.SchemaField("is_flagged_for_review", "BOOLEAN", mode="REQUIRED", description="Boolean flag indicating whether the transaction is flagged for risk review"),
        bigquery.SchemaField("rolling_balance_30d", "NUMERIC", mode="NULLABLE", description="The 30-day rolling average of account balances"),
        bigquery.SchemaField("consecutive_high_risk", "INTEGER", mode="NULLABLE", description="Indicator of consecutive high-risk category spends"),
        bigquery.SchemaField("delinquency_risk_score", "INTEGER", mode="NULLABLE", description="The calculated credit delinquency risk score (0-100)")
    ]

    job_config = bigquery.LoadJobConfig(
        schema=schema,
        source_format=bigquery.SourceFormat.CSV,
        skip_leading_rows=1,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,  # Overwrite table during deploy for fresh dataset
    )

    try:
        load_job = bq_client.load_table_from_uri(
            gcs_uri,
            table_ref,
            job_config=job_config
        )
        logger.info("Ingestion job started. Job ID: %s", load_job.job_id)
        logger.info("Waiting for data load to complete...")
        
        # Wait for the load job to complete (blocking)
        result = load_job.result()
        
        if load_job.errors:
            logger.error("Ingestion job completed with errors:")
            for err in load_job.errors:
                logger.error("  - %s", err)
            sys.exit(1)

        logger.info("=================================================================")
        logger.info("DEPLOYMENT COMPLETE!")
        logger.info("Successfully loaded %s records into BigQuery table: %s", f"{result.output_rows:,}", table_ref)
        logger.info("=================================================================")
        sys.exit(0)

    except GoogleAPIError as e:
        logger.error("BigQuery loading job failed: %s", e)
        sys.exit(1)

if __name__ == "__main__":
    run_deployment()
