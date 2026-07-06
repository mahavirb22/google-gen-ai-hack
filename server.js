import express from 'express';
import cors from 'cors';
import { BigQuery } from '@google-cloud/bigquery';
import { GoogleGenAI } from '@google/genai';
import 'dotenv/config';

const app = express();
const port = process.env.PORT || 8080;

// Enable CORS and JSON body parser middleware
app.use(cors());
app.use(express.json());

// Initialize BigQuery Client
// The client will automatically discover credentials via the GOOGLE_APPLICATION_CREDENTIALS env variable
// or from GCE/GCF metadata servers when deployed in Google Cloud.
const bigquery = new BigQuery({
  projectId: process.env.GCP_PROJECT_ID,
});

// Initialize Gemini Client
// The new @google/genai SDK automatically retrieves GEMINI_API_KEY from process.env.GEMINI_API_KEY
const ai = new GoogleGenAI({ apiKey: process.env.GEMINI_API_KEY });

/**
 * Health check endpoint
 */
app.get('/health', (req, res) => {
  return res.status(200).json({
    status: 'OK',
    timestamp: new Date().toISOString(),
    message: 'Pre-Delinquency Intervention API Service is running'
  });
});

/**
 * GET /api/high-risk-users
 * Queries BigQuery for users possessing a delinquency_risk_score > 80.
 * Projects the max score and aggregate counts for the dashboard representation.
 */
app.get('/api/high-risk-users', async (req, res) => {
  const datasetId = process.env.BQ_DATASET_ID || 'delinquency_engine';
  const tableId = process.env.BQ_TABLE_ID || 'transactions';
  
  let projectId = process.env.GCP_PROJECT_ID;
  try {
    if (!projectId) {
      projectId = await bigquery.getProjectId();
    }
  } catch (err) {
    console.error('Failed to auto-discover GCP Project ID from BigQuery Client:', err.message);
    return res.status(500).json({
      success: false,
      error: 'GCP Project ID configuration missing'
    });
  }

  const query = `
    WITH user_metrics AS (
      SELECT 
        user_id,
        MAX(delinquency_risk_score) as max_risk_score,
        COUNT(timestamp) as total_tx_count,
        CAST(ROUND(AVG(account_balance), 2) AS FLOAT64) as avg_balance
      FROM \`${projectId}.${datasetId}.${tableId}\`
      GROUP BY user_id
    )
    SELECT 
      user_id,
      max_risk_score,
      total_tx_count as transaction_count,
      avg_balance as average_balance
    FROM user_metrics
    WHERE max_risk_score > 80
    ORDER BY max_risk_score DESC
  `;

  try {
    console.log(`Executing BigQuery query on target table: ${projectId}.${datasetId}.${tableId}`);
    const [rows] = await bigquery.query({
      query: query,
      useLegacySql: false,
    });

    return res.status(200).json({
      success: true,
      count: rows.length,
      users: rows
    });
  } catch (error) {
    console.error('BigQuery query invocation failed:', error);
    return res.status(500).json({
      success: false,
      error: 'Failed to query high-risk users from BigQuery data warehouse',
      details: error.message
    });
  }
});

/**
 * GET /api/user-transactions/:userId
 * Queries BigQuery for the actual transaction history of a specific user.
 * Sorted chronologically so that the dashboard and Gemini read the sequential flow.
 */
app.get('/api/user-transactions/:userId', async (req, res) => {
  const { userId } = req.params;
  const datasetId = process.env.BQ_DATASET_ID || 'delinquency_engine';
  const tableId = process.env.BQ_TABLE_ID || 'transactions';
  let projectId = process.env.GCP_PROJECT_ID;
  
  try {
    if (!projectId) {
      projectId = await bigquery.getProjectId();
    }
  } catch (err) {
    return res.status(500).json({ success: false, error: 'GCP Project ID missing' });
  }

  const query = `
    SELECT 
      FORMAT_TIMESTAMP('%Y-%m-%d %H:%M:%S', timestamp) as timestamp, 
      CAST(transaction_amount AS FLOAT64) as transaction_amount, 
      merchant_category, 
      CAST(account_balance AS FLOAT64) as account_balance, 
      is_flagged_for_review,
      CAST(delinquency_risk_score AS INT64) as delinquency_risk_score
    FROM \`${projectId}.${datasetId}.${tableId}\`
    WHERE user_id = @userId
    ORDER BY timestamp ASC
    LIMIT 100
  `;

  try {
    console.log(`Fetching transactions for user: ${userId} from BigQuery...`);
    const [rows] = await bigquery.query({
      query: query,
      params: { userId: userId },
      useLegacySql: false,
    });

    return res.status(200).json({
      success: true,
      user_id: userId,
      count: rows.length,
      transactions: rows
    });
  } catch (error) {
    console.error(`Failed to fetch transactions for user ${userId}:`, error);
    return res.status(500).json({
      success: false,
      error: 'Failed to query user transactions',
      details: error.message
    });
  }
});

/**
 * POST /api/generate-explanation
 * Accept transaction history for a user and leverage Gemini 2.5 Flash
 * to generate a 3-sentence XAI (Explainable AI) summary.
 */
app.post('/api/generate-explanation', async (req, res) => {
  const { transaction_history } = req.body;

  if (!transaction_history || !Array.isArray(transaction_history)) {
    return res.status(400).json({
      success: false,
      error: 'Invalid payload: transaction_history array is required in the body.'
    });
  }

  if (transaction_history.length === 0) {
    return res.status(400).json({
      success: false,
      error: 'Invalid payload: transaction_history array cannot be empty.'
    });
  }

  // Format data clearly to feed into Gemini context
  const historySnippet = JSON.stringify(transaction_history.slice(0, 50), null, 2);

  const prompt = `
You are a senior credit risk analyst and Explainable AI (XAI) generator.
Analyze the following customer transaction history snippet.
Provide a clear, narrative explanation of why this user is exhibiting pre-delinquent behavior.
The output MUST be exactly three (3) sentences long. 
Focus on critical features such as balance trends, transaction size compared to balance, and consecutive high-risk merchant categories (Gambling, Cash Advance, Crypto Exchange, Payday Lender).

Transaction History:
${historySnippet}

Explainable AI (XAI) Summary:
`;

  try {
    console.log('Sending transaction data to Gemini API (gemini-2.5-flash)...');
    
    // Call the official modern @google/genai API
    const response = await ai.models.generateContent({
      model: 'gemini-2.5-flash',
      contents: prompt,
    });

    const explanation = response.text ? response.text.trim() : 'Failed to generate summary.';

    return res.status(200).json({
      success: true,
      explanation: explanation
    });
  } catch (error) {
    console.error('Gemini content generation failed:', error);
    return res.status(500).json({
      success: false,
      error: 'Failed to generate risk explanation via Gemini API',
      details: error.message
    });
  }
});

// Start the Express Service
app.listen(port, () => {
  console.log('====================================================');
  console.log(`Pre-Delinquency API Backend running on port ${port}`);
  console.log('====================================================');
});
