#!/usr/bin/env python3
"""
High-Performance Synthetic Financial Data Generator.
Uses NumPy and Faker to generate millions of transaction rows,
intentionally seeding pre-delinquent spending profiles.
"""

import argparse
import sys
import time
import logging
import numpy as np
import pandas as pd
from faker import Faker

# Setup logging to stderr
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)]
)
logger = logging.getLogger("generate_synthetic_data")

HIGH_RISK_CATEGORIES = ["Online Gambling", "Cash Advance", "Crypto Exchange", "Payday Lender"]

def generate_data(size: int, output_path: str):
    logger.info("Initializing generator for %s rows...", f"{size:,}")
    start_time = time.time()
    
    # Initialize Faker
    fake = Faker()
    fake.seed_instance(42)
    np.random.seed(42)
    
    # 1. Create a pool of merchant names/categories to keep sampling vectorized
    logger.info("Creating realistic merchant category mappings...")
    merchant_categories = {
        "Grocery": [f"{fake.company()} Foods" for _ in range(15)],
        "Gas Station": [f"{fake.company()} Petroleum" for _ in range(10)],
        "Restaurant": [f"The {fake.word().capitalize()} Bistro" for _ in range(20)],
        "Utility": [f"{fake.state()} Power & Light" for _ in range(5)],
        "Rent": ["Property Management LLC", "Housing Association"],
        "Online Gambling": ["Spin Palace", "Jackpot City", "Casino Royal", "BetStar Online", "Apex Betting"],
        "Cash Advance": ["QuickCash", "FastLoan Advance", "Speedy Cash", "Instant Money Inc"],
        "Crypto Exchange": ["CoinVault", "CryptoDepot", "BitTrade", "BlockExchange"],
        "Payday Lender": ["Paycheck Advance", "Easy Money Loans", "LendSmart", "Capital Loans"],
        "Retail": [f"{fake.company()} Stores" for _ in range(25)],
        "Online Shopping": ["ZonMarkt", "QuickShip", "CartFlow", "ShopStream"],
        "Travel": ["GlobeTrek Airlines", "OceanView Resorts", "TransitExpress"]
    }
    
    # Pre-calculate category probabilities (mostly standard spending, some high-risk)
    categories = list(merchant_categories.keys())
    category_probs = [0.22, 0.15, 0.15, 0.10, 0.05, 0.015, 0.015, 0.015, 0.015, 0.14, 0.08, 0.05]
    
    # 2. Vectorized generation of standard transactions
    logger.info("Generating base transactions via NumPy...")
    
    # Determine users count dynamically
    num_users = max(500, size // 1000)
    users = [f"USER_{i:05d}" for i in range(num_users)]
    
    # Randomly assign standard columns
    user_ids = np.random.choice(users, size)
    
    # Timestamps within 90 days
    start_date = np.datetime64('2026-04-01T00:00:00')
    random_offsets = np.random.randint(0, 7776000, size) # 90 days in seconds
    timestamps = start_date + random_offsets.astype('timedelta64[s]')
    
    # Transaction amounts (lognormal distribution)
    amounts = np.random.lognormal(mean=3.4, sigma=0.9, size=size).round(2)
    
    # Merchant categories
    chosen_categories = np.random.choice(categories, size, p=category_probs)
    
    # Map categories to specific merchants
    # Pre-select index mapping per category to optimize execution speed
    chosen_merchants = []
    for cat in chosen_categories:
        merchant_list = merchant_categories[cat]
        # Quick random select
        chosen_merchants.append(np.random.choice(merchant_list))
    
    # Initial Balances (between $500 and $15,000)
    balances = np.random.uniform(500, 15000, size).round(2)
    
    # Flags
    is_flagged = np.random.choice([True, False], size, p=[0.01, 0.99])
    
    # Combine into DataFrame
    logger.info("Assembling core DataFrame...")
    df = pd.DataFrame({
        'user_id': user_ids,
        'timestamp': timestamps,
        'transaction_amount': amounts,
        'merchant_category': chosen_categories,
        'account_balance': balances,
        'is_flagged_for_review': is_flagged
    })
    
    # 3. Inject "Perfect Pre-Delinquent Profiles"
    # We will pick 5% of users to exhibit extreme pre-delinquent behaviors:
    # A starting balance that drops by > 40% while making 5 consecutive high-risk purchases.
    logger.info("Injecting target pre-delinquent profiles...")
    
    # Select delinquent users
    delinquent_users = users[:int(num_users * 0.05)]
    logger.info("Programmed %d users with explicit pre-delinquency behaviors.", len(delinquent_users))
    
    # To keep execution extremely fast, we modify their records in place
    # Group the dataframe by user_id to easily locate transactions for the target users
    df_sorted = df.sort_values(by=['user_id', 'timestamp']).reset_index(drop=True)
    
    # Get starting indices for each user
    user_change_indices = df_sorted['user_id'].ne(df_sorted['user_id'].shift()).to_numpy().nonzero()[0]
    
    # Create index map
    user_to_idx = {}
    for idx in range(len(user_change_indices)):
        start = user_change_indices[idx]
        end = user_change_indices[idx + 1] if idx + 1 < len(user_change_indices) else len(df_sorted)
        user_to_idx[df_sorted.iloc[start]['user_id']] = (start, end)
        
    # Overwrite segments for delinquent users
    for d_user in delinquent_users:
        if d_user not in user_to_idx:
            continue
        start_idx, end_idx = user_to_idx[d_user]
        user_length = end_idx - start_idx
        
        # Ensure they have at least 10 transactions to show a trend
        if user_length < 10:
            continue
            
        # We will modify the last 5 transactions of their history
        mod_start = end_idx - 5
        
        # Define starting balance for the drop sequence (lower starting balance with sharper drops)
        starting_balance = 1000.00
        current_balance = starting_balance
        
        # 5 consecutive high risk categories
        categories_seq = ["Online Gambling", "Cash Advance", "Online Gambling", "Payday Lender", "Online Gambling"]
        
        for offset, cat in enumerate(categories_seq):
            idx = mod_start + offset
            # 19% drop per transaction (total 95% drop, down to $50)
            amount = round(starting_balance * 0.19, 2)
            current_balance = round(current_balance - amount, 2)
            
            # Apply modifications
            df_sorted.at[idx, 'transaction_amount'] = amount
            df_sorted.at[idx, 'merchant_category'] = cat
            df_sorted.at[idx, 'account_balance'] = current_balance
            # Flag the last transaction in the chain
            if offset == 4:
                df_sorted.at[idx, 'is_flagged_for_review'] = True
                
        # Set the preceding transactions for this user to align with the starting balance
        # to show a healthy history before the sudden pre-delinquent drop
        for idx in range(start_idx, mod_start):
            df_sorted.at[idx, 'account_balance'] = starting_balance + (mod_start - idx) * 100.00
            df_sorted.at[idx, 'merchant_category'] = "Grocery"
            df_sorted.at[idx, 'transaction_amount'] = 50.00
            df_sorted.at[idx, 'is_flagged_for_review'] = False

    # 4. Save to CSV
    logger.info("Writing dataset directly to %s...", output_path)
    df_sorted.to_csv(output_path, index=False)
    
    elapsed = time.time() - start_time
    logger.info("Generated %s rows in %.2f seconds.", f"{len(df_sorted):,}", elapsed)

def main():
    parser = argparse.ArgumentParser(
        description="High-Speed Faker & NumPy Synthetic Data Generator"
    )
    parser.add_argument(
        "--size",
        type=int,
        default=5000000,
        help="Total rows to generate (default: 5,000,000)"
    )
    parser.add_argument(
        "--output",
        type=str,
        default="transactions_raw.csv",
        help="Output CSV filename (default: transactions_raw.csv)"
    )
    
    args = parser.parse_args()
    
    try:
        generate_data(args.size, args.output)
        sys.exit(0)
    except Exception as e:
        logger.error("An error occurred during data generation: %s", e)
        sys.exit(1)

if __name__ == "__main__":
    main()
