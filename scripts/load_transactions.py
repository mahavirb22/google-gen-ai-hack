#!/usr/bin/env python3
"""
Python script to programmatically load a massive CSV file from Google Cloud Storage
into Google BigQuery.

Designed for the Pre-Delinquency Intervention Engine.
"""

import argparse
import sys
import logging
from google.cloud import bigquery
from google.api_core.exceptions import GoogleAPIError

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("load_transactions")

def load_csv_from_gcs_to_bigquery(
    gcs_uri: str,
    project_id: str,
    dataset_id: str,
    table_id: str,
    write_disposition: str = "WRITE_APPEND"
) -> bool:
    """
    Loads a CSV file from a Google Cloud Storage bucket into a BigQuery table.

    Args:
        gcs_uri: The GCS URI of the CSV file (e.g. gs://bucket-name/file.csv)
        project_id: The GCP Project ID.
        dataset_id: The BigQuery dataset ID.
        table_id: The BigQuery table name.
        write_disposition: How to handle existing data (WRITE_APPEND or WRITE_TRUNCATE).

    Returns:
        True if the job completed successfully, False otherwise.
    """
    logger.info("Initializing BigQuery Client for project: %s", project_id)
    try:
        # Initialize client. The client will automatically discover credentials from
        # the environment (e.g., GOOGLE_APPLICATION_CREDENTIALS) or metadata server.
        client = bigquery.Client(project=project_id)
    except Exception as e:
        logger.error("Failed to initialize BigQuery client: %s", e)
        return False

    table_ref = f"{project_id}.{dataset_id}.{table_id}"
    logger.info("Target BigQuery Table: %s", table_ref)
    logger.info("Source GCS URI: %s", gcs_uri)

    # Define explicit schema to match transactions.sql.
    # Specifying the schema explicitly is a production best-practice. It avoids schema
    # autodetection errors, enforces data types (especially NUMERIC for financial precision),
    # and secures schema stability.
    schema = [
        bigquery.SchemaField("user_id", "STRING", mode="REQUIRED", description="Unique identifier for the customer/user"),
        bigquery.SchemaField("timestamp", "TIMESTAMP", mode="REQUIRED", description="The date and time when the transaction occurred"),
        bigquery.SchemaField("transaction_amount", "NUMERIC", mode="REQUIRED", description="The amount of the transaction"),
        bigquery.SchemaField("merchant_category", "STRING", mode="NULLABLE", description="The industry category of the merchant"),
        bigquery.SchemaField("account_balance", "NUMERIC", mode="REQUIRED", description="The user's account balance immediately after the transaction"),
        bigquery.SchemaField("is_flagged_for_review", "BOOLEAN", mode="REQUIRED", description="Boolean flag indicating whether the transaction is flagged for risk review"),
    ]

    # Configure the load job options
    job_config = bigquery.LoadJobConfig(
        schema=schema,
        source_format=bigquery.SourceFormat.CSV,
        # Set skip_leading_rows to 1 because the CSV has a header row
        skip_leading_rows=1,
        # WRITE_APPEND: appends to table, WRITE_TRUNCATE: overwrites, WRITE_EMPTY: errors if not empty
        write_disposition=write_disposition,
    )

    try:
        logger.info("Starting load job...")
        load_job = client.load_table_from_uri(
            gcs_uri,
            table_ref,
            job_config=job_config
        )
        
        logger.info("Load job submitted successfully. Job ID: %s", load_job.job_id)
        logger.info("Waiting for job to complete...")
        
        # Wait for the load job to complete. This is synchronous blocking.
        # In production environments, consider setting a timeout or checking status asynchronously.
        result = load_job.result()
        
        # Verify if the job encountered any errors
        if load_job.errors:
            logger.error("Job completed with errors:")
            for err in load_job.errors:
                logger.error("  - %s", err)
            return False

        logger.info(
            "Successfully loaded %s rows into %s.",
            result.output_rows,
            table_ref
        )
        return True

    except GoogleAPIError as api_err:
        logger.error("Google Cloud API error occurred during the load job: %s", api_err)
        return False
    except Exception as e:
        logger.error("An unexpected error occurred during ingestion: %s", e)
        return False

def main():
    parser = argparse.ArgumentParser(
        description="Load transaction CSV file from GCS to BigQuery."
    )
    parser.add_argument(
        "--gcs-uri",
        required=True,
        help="GCS URI of the source CSV file (e.g. gs://my-bucket/transactions.csv)"
    )
    parser.add_argument(
        "--project-id",
        required=True,
        help="GCP Project ID"
    )
    parser.add_argument(
        "--dataset-id",
        default="delinquency_engine",
        help="BigQuery dataset ID (default: delinquency_engine)"
    )
    parser.add_argument(
        "--table-id",
        default="transactions",
        help="BigQuery table ID (default: transactions)"
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite target table instead of appending (uses WRITE_TRUNCATE)"
    )

    args = parser.parse_args()

    write_disp = "WRITE_TRUNCATE" if args.overwrite else "WRITE_APPEND"

    success = load_csv_from_gcs_to_bigquery(
        gcs_uri=args.gcs_uri,
        project_id=args.project_id,
        dataset_id=args.dataset_id,
        table_id=args.table_id,
        write_disposition=write_disp
    )

    if success:
        logger.info("Ingestion process completed successfully.")
        sys.exit(0)
    else:
        logger.error("Ingestion process failed.")
        sys.exit(1)

if __name__ == "__main__":
    main()
