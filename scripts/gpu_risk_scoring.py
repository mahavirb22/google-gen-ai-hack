#!/usr/bin/env python3
"""
NVIDIA cuDF-Accelerated Pre-Delinquency Risk Scoring Pipeline.
Designed for high-performance ETL and feature engineering on GPUs using RAPIDS.
"""

import sys
import argparse
import logging
import time
import subprocess
import re

# 1. Parse arguments before importing pandas/numpy to set up cudf.pandas import hook
parser = argparse.ArgumentParser(
    description="NVIDIA cuDF-Accelerated Pre-Delinquency Risk Scoring Pipeline"
)
parser.add_argument(
    "--mode",
    choices=["cpu", "gpu", "benchmark"],
    default="benchmark",
    help="Execution mode (default: benchmark)"
)
parser.add_argument(
    "--size",
    type=int,
    default=1000000,
    help="Number of rows for synthetic benchmark data (default: 1,000,000)"
)
parser.add_argument(
    "--input",
    type=str,
    help="Path to input CSV file (disables synthetic data generation)"
)
parser.add_argument(
    "--output",
    type=str,
    help="Path to save output CSV file with risk scores"
)
args, unknown = parser.parse_known_args()

# 2. Programmatically activate cudf.pandas (zero-copy GPU acceleration) if in gpu mode
if args.mode == "gpu":
    try:
        import cudf.pandas
        cudf.pandas.install()
        print(">>> SUCCESS: cudf.pandas hook installed. GPU Acceleration Active! <<<", file=sys.stderr)
    except ImportError:
        print(">>> WARNING: cudf.pandas not available. Falling back to CPU. <<<", file=sys.stderr)

# 3. Import pandas and numpy (if in gpu mode, pandas is now backed by cudf)
import pandas as pd
import numpy as np

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stderr)  # Log to stderr to avoid polluting stdout metrics
    ]
)
logger = logging.getLogger("gpu_risk_scoring")

HIGH_RISK_CATEGORIES = {'Gambling', 'Online Gambling', 'Cash Advance', 'Crypto Exchange', 'Payday Lender'}

def generate_synthetic_data(size: int) -> pd.DataFrame:
    """
    Generates synthetic transactional data for scaling and benchmarking tests.
    """
    logger.info("Generating %s rows of synthetic transactions...", f"{size:,}")
    np.random.seed(42)
    
    # Distribute users dynamically based on dataset size
    num_users = max(100, size // 1000)
    users = [f"USER_{i:04d}" for i in range(num_users)]
    user_ids = np.random.choice(users, size)
    
    # Generate random timestamps within a 90-day window
    start_date = np.datetime64('2026-04-01T00:00:00')
    random_offsets = np.random.randint(0, 7776000, size)  # 90 days in seconds
    timestamps = start_date + random_offsets.astype('timedelta64[s]')
    
    # Transaction amounts (lognormal distribution to simulate financial transactions)
    transaction_amounts = np.random.lognormal(mean=3.5, sigma=1.0, size=size).round(2)
    
    # Merchant categories with varying risk weights
    categories = [
        'Grocery', 'Gas Station', 'Restaurant', 'Utility', 'Rent', 
        'Gambling', 'Cash Advance', 'Crypto Exchange', 'Payday Lender',
        'Retail', 'Online Shopping', 'Travel'
    ]
    probs = [0.2, 0.15, 0.15, 0.1, 0.05, 0.02, 0.01, 0.01, 0.01, 0.15, 0.1, 0.05]
    merchant_categories = np.random.choice(categories, size, p=probs)
    
    # Account balances
    account_balances = np.random.uniform(10, 15000, size).round(2)
    
    # System risk review flag
    is_flagged = np.random.choice([True, False], size, p=[0.02, 0.98])
    
    df = pd.DataFrame({
        'user_id': user_ids,
        'timestamp': timestamps,
        'transaction_amount': transaction_amounts,
        'merchant_category': merchant_categories,
        'account_balance': account_balances,
        'is_flagged_for_review': is_flagged
    })
    
    return df

def run_feature_engineering(df: pd.DataFrame) -> pd.DataFrame:
    """
    Executes the ETL and feature engineering pipeline:
    - 30-day rolling average of account balances.
    - Consecutive high-risk merchant category flag.
    - Calculates a 0-100 delinquency risk score.
    """
    logger.info("Starting feature engineering pipeline...")
    
    # Ensure timestamp is datetime and sort
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.sort_values(by=['user_id', 'timestamp']).reset_index(drop=True)
    
    # Feature 1: 30-day rolling average of account balances
    logger.info("Computing 30-day rolling balance averages per user...")
    df_temp = df.set_index('timestamp')
    # rolling_bal returns a series indexed by (user_id, timestamp) sorted by keys
    rolling_bal = df_temp.groupby('user_id')['account_balance'].rolling('30D', min_periods=1).mean()
    # Align the sorted series back directly and round to 2 decimal places for BQ NUMERIC compatibility
    df['rolling_balance_30d'] = np.round(rolling_bal.values, 2)
    
    # Feature 2: Flags consecutive (consecutively ordered) high-risk merchant categories
    logger.info("Flagging consecutive high-risk merchant category spends...")
    df['is_high_risk_mc'] = df['merchant_category'].isin(HIGH_RISK_CATEGORIES).astype(int)
    df['prev_high_risk_mc'] = df.groupby('user_id')['is_high_risk_mc'].shift(1).fillna(0).astype(int)
    df['consecutive_high_risk'] = ((df['is_high_risk_mc'] == 1) & (df['prev_high_risk_mc'] == 1)).astype(int)
    
    # Feature 3: Delinquency Risk Score calculation (0 to 100 scale)
    logger.info("Calculating delinquency risk scores...")
    
    # Component A: Account balance depletion compared to 30d rolling average (max 30 pts)
    # Handle division by zero with small constant
    balance_depletion = (df['rolling_balance_30d'] - df['account_balance']) / (df['rolling_balance_30d'] + 1e-5)
    balance_depletion_score = (balance_depletion * 30).clip(0, 30)
    
    # Component B: Back-to-back transactions in high risk categories (max 25 pts)
    consecutive_high_risk_score = df['consecutive_high_risk'] * 25
    
    # Component C: Already flagged for manual review (max 20 pts)
    flagged_score = df['is_flagged_for_review'].astype(int) * 20
    
    # Component D: Low nominal balance indicator (max 15 pts for balance tending to 0)
    low_balance_score = ((100 - df['account_balance']) / 100 * 15).clip(0, 15)
    
    # Component E: Transaction size relative to current balance (max 10 pts)
    leverage_ratio = df['transaction_amount'] / (df['account_balance'] + 1e-5)
    leverage_score = (leverage_ratio * 10).clip(0, 10)
    
    # Aggregated score
    df['delinquency_risk_score'] = (
        balance_depletion_score +
        consecutive_high_risk_score +
        flagged_score +
        low_balance_score +
        leverage_score
    ).clip(0, 100).round().astype(int)
    
    # Clean up intermediate metrics
    df = df.drop(columns=['is_high_risk_mc', 'prev_high_risk_mc'])
    
    logger.info("Feature engineering pipeline completed.")
    return df

def run_benchmark_orchestration(size: int):
    """
    Spawns standard CPU and GPU subprocesses to record performance.
    Outputs a comparison report.
    """
    print("=================================================================", flush=True)
    print(f"Starting GPU vs CPU Performance Benchmark (Dataset: {size:,} rows)", flush=True)
    print("=================================================================", flush=True)
    
    # 1. Run CPU Subprocess
    print("Running CPU Baseline (Standard Pandas)...", flush=True)
    cpu_proc = subprocess.run(
        [sys.executable, __file__, "--mode", "cpu", "--size", str(size)],
        capture_output=True,
        text=True
    )
    
    # 2. Run GPU Subprocess
    print("Running GPU Accelerated (NVIDIA RAPIDS cudf.pandas)...", flush=True)
    gpu_proc = subprocess.run(
        [sys.executable, __file__, "--mode", "gpu", "--size", str(size)],
        capture_output=True,
        text=True
    )
    
    # Parse elapsed pipeline execution times from outputs
    cpu_time = None
    gpu_time = None
    
    cpu_match = re.search(r"\[METRIC\] pipeline_execution_seconds: ([\d\.]+)", cpu_proc.stdout)
    if cpu_match:
        cpu_time = float(cpu_match.group(1))
        
    gpu_match = re.search(r"\[METRIC\] pipeline_execution_seconds: ([\d\.]+)", gpu_proc.stdout)
    if gpu_match:
        gpu_time = float(gpu_match.group(1))
        
    # Output Benchmark Summary
    print("\n================ BENCHMARK RESULTS ================", flush=True)
    if cpu_time is not None:
        print(f"CPU Execution Time : {cpu_time:.4f} seconds", flush=True)
    else:
        print("CPU Execution Time : FAILED / CRASHED", flush=True)
        print("CPU Stderr:\n", cpu_proc.stderr, flush=True)
        
    if gpu_time is not None:
        print(f"GPU Execution Time : {gpu_time:.4f} seconds", flush=True)
    else:
        # Check if the process exited with an import warning or fallback
        if "WARNING: cudf.pandas not available" in gpu_proc.stderr:
            print("GPU Execution Time : NOT ACCELERATED (NVIDIA RAPIDS / GPU not found in environment)", flush=True)
        else:
            print("GPU Execution Time : FAILED / CRASHED", flush=True)
        print("GPU Stderr:\n", gpu_proc.stderr, flush=True)
        
    if cpu_time and gpu_time and gpu_proc.returncode == 0 and "SUCCESS: cudf.pandas" in gpu_proc.stderr:
        speedup = cpu_time / gpu_time
        print(f"GPU Speedup Factor : {speedup:.2f}x", flush=True)
        print("===================================================", flush=True)
        print("🚀 HACKATHON WINNER: NVIDIA RAPIDS GPU ACCELERATION!", flush=True)
    else:
        print("===================================================", flush=True)
        print("Note: To achieve true GPU speedup, run this on a GCP GPU instance with RAPIDS installed.", flush=True)
    print("===================================================\n", flush=True)

def main():
    if args.mode == "benchmark":
        run_benchmark_orchestration(args.size)
        sys.exit(0)

    # In individual CPU/GPU modes:
    logger.info("Executing in %s mode...", args.mode.upper())
    
    if args.input:
        logger.info("Reading input data from: %s", args.input)
        df = pd.read_csv(args.input)
    else:
        df = generate_synthetic_data(args.size)

    # Benchmark CPU or GPU pipeline calculation
    start_time = time.time()
    df_processed = run_feature_engineering(df)
    end_time = time.time()
    elapsed = end_time - start_time

    # Output execution metrics for benchmarking orchestrator
    print(f"[METRIC] pipeline_execution_seconds: {elapsed}")

    if args.output:
        logger.info("Writing output data to: %s", args.output)
        df_processed.to_csv(args.output, index=False)
        logger.info("Output successfully written.")

if __name__ == "__main__":
    main()
