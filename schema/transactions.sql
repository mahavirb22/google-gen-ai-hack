-- BigQuery Standard SQL Schema for Transactions Table
-- Updated to include feature-engineered risk columns

CREATE OR REPLACE TABLE `delinquency_engine.transactions`
(
  user_id STRING NOT NULL OPTIONS(description="Unique identifier for the customer/user"),
  timestamp TIMESTAMP NOT NULL OPTIONS(description="The date and time when the transaction occurred"),
  transaction_amount NUMERIC NOT NULL OPTIONS(description="The amount of the transaction. Stored as NUMERIC to prevent floating point inaccuracies"),
  merchant_category STRING OPTIONS(description="The industry category of the merchant (e.g., MCC code or textual description)"),
  account_balance NUMERIC NOT NULL OPTIONS(description="The user's account balance immediately after the transaction"),
  is_flagged_for_review BOOLEAN NOT NULL OPTIONS(description="Boolean flag indicating whether the transaction is flagged for risk review"),
  rolling_balance_30d NUMERIC OPTIONS(description="The 30-day rolling average of account balances"),
  consecutive_high_risk INT64 OPTIONS(description="Indicator if transaction occurred consecutively in high-risk categories"),
  delinquency_risk_score INT64 OPTIONS(description="The calculated credit delinquency risk score (0-100)")
)
PARTITION BY DATE(timestamp)
CLUSTER BY user_id, merchant_category
OPTIONS(
  description="Transactional history table for pre-delinquency evaluation with GPU-engineered risk scores, partitioned by day and clustered by user_id and merchant_category."
);
