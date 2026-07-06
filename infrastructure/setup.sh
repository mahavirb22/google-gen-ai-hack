#!/usr/bin/env bash

# Exit immediately if a command exits with a non-zero status
set -euo pipefail

# Infrastructure Configuration
# Feel free to adjust these environment variables before running
PROJECT_ID=${GCP_PROJECT_ID:-"your-gcp-project-id"}
REGION=${GCP_REGION:-"us-central1"}
BUCKET_NAME=${GCS_BUCKET_NAME:-"${PROJECT_ID}-delinquency-raw-data"}
DATASET_NAME=${BQ_DATASET_NAME:-"delinquency_engine"}

echo "================================================================="
echo "Configuring GCP Infrastructure for Pre-Delinquency Engine"
echo "Project ID:   ${PROJECT_ID}"
echo "Region:       ${REGION}"
echo "GCS Bucket:   gs://${BUCKET_NAME}"
echo "BQ Dataset:   ${DATASET_NAME}"
echo "================================================================="

# Set the active project in gcloud configuration
echo "Setting gcloud project context..."
gcloud config set project "${PROJECT_ID}"

# 1. Create Google Cloud Storage Bucket
# --location: Specifies the location of the bucket (e.g. us-central1)
# --uniform-bucket-level-access: Enables uniform bucket-level access for better security controls
# --public-access-prevention: Enforces public access prevention to secure sensitive financial data
echo "Creating Google Cloud Storage bucket..."
if gsutil ls -b "gs://${BUCKET_NAME}" >/dev/null 2>&1; then
    echo "GCS bucket gs://${BUCKET_NAME} already exists. Skipping creation."
else
    gcloud storage buckets create "gs://${BUCKET_NAME}" \
        --location="${REGION}" \
        --uniform-bucket-level-access \
        --public-access-prevention
    echo "GCS bucket created successfully."
fi

# 2. Create BigQuery Dataset
# --location: The geographic location where the dataset should reside. Must match GCS bucket region.
# --description: Human-readable description of the dataset
echo "Creating BigQuery dataset..."
if bq show --dataset "${PROJECT_ID}:${DATASET_NAME}" >/dev/null 2>&1; then
    echo "BigQuery dataset ${DATASET_NAME} already exists. Skipping creation."
else
    bq --location="${REGION}" mk \
        --dataset \
        --description="Dataset for the Pre-Delinquency Intervention Engine" \
        "${PROJECT_ID}:${DATASET_NAME}"
    echo "BigQuery dataset created successfully."
fi

echo "Infrastructure setup completed successfully!"
