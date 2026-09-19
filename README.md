# Monday.com Business Intelligence Agent

## 1. Project Overview

The Monday.com Business Intelligence Agent is a conversational Streamlit application designed for founders and executives of Skylark Drones. It connects to monday.com boards containing Deals and Work Orders data and answers business questions using predefined analytics tools.

The agent uses Groq for natural-language understanding and tool selection. Business calculations are performed by Python analytics functions, ensuring that the language model does not independently invent or calculate business figures.


## 🔗 Hosted Prototype

[Open the Monday.com Business Intelligence Agent](https://monday-bi-agent-cg6dhc45a4uckcwpx7jmcp.streamlit.app/)

This hosted Streamlit prototype provides a conversational interface for analyzing data from the monday.com **Deals** and **Work Orders** boards. Users can ask business and operational questions, review analytics, and receive responses with relevant data caveats.

## 2. Main Features

- Read-only integration with monday.com through the API.
- Dynamic retrieval of Deals and Work Orders data.
- Pipeline value analysis, including sector filters and outlier comparisons.
- Revenue analysis from won deals.
- Sector-level breakdowns.
- Work Order billing-status analysis.
- Surveyed quantity analysis, including optional work-type filters.
- Data-quality and caveat reporting.
- Conversational question answering.
- Clarifying questions for ambiguous requests.
- Handling of missing values, inconsistent formats, and invalid tool arguments.

## 3. Architecture Overview

```text
User
  |
  v
Streamlit Conversational Interface
  |
  v
Groq AI Agent
  |
  v
Tool Selection / Function Calling
  |
  v
Python Analytics Functions
  |
  v
Monday.com API
  |
  +--> Deals Board
  |
  +--> Work Orders Board
  |
  v
Cleaned Data + Analytics Result
  |
  v
Founder-Friendly Response
```

## 4. Project Structure

```text
monday-bi-agent/
|
|-- app.py
|-- requirements.txt
|-- README.md
|-- bi_agent/
|   |-- agent.py
|   |-- analytics.py
|   |-- config.py
|   |-- data_source.py
|   |-- ...
|-- .gitignore
```

## 5. Technology Stack

- **Python:** Application and analytics logic.
- **Streamlit:** Browser-based conversational user interface.
- **Groq:** Language model for query understanding, tool selection, and response phrasing.
- **monday.com API:** Dynamic read-only access to Deals and Work Orders boards.
- **JSON:** Tool-call arguments and structured analytics results.

## 6. Configuration

Do not hardcode credentials in the source code.

### Local `.env` configuration

Create a `.env` file in the project root:

```env
MONDAY_API_TOKEN=your_monday_api_token
GROQ_API_KEY=your_groq_api_key
```

Add any board IDs or other configuration values required by `config.py`.

### Streamlit Cloud configuration

Open the deployed application settings and add the required values under **Secrets**:

```toml
MONDAY_API_TOKEN = "your_monday_api_token"
GROQ_API_KEY = "your_groq_api_key"
```

Use the exact variable names expected by the configuration module. Never commit `.env` files, API keys, or private tokens to GitHub.

## 7. monday.com Setup

1. Create or open a monday.com workspace.
2. Import the Deals spreadsheet as a separate board.
3. Import the Work Orders spreadsheet as another separate board.
4. Configure suitable column types for text, numbers, dates, status, and amounts.
5. Create or obtain a monday.com API token with read-only access where possible.
6. Record the board IDs for both boards.
7. Add the token and board IDs to the local environment or Streamlit Secrets.
8. Confirm that the application can read data from both boards.

The application must query monday.com dynamically. Spreadsheet data should not be hardcoded into the application.

## 8. Installation

Clone the repository:

```bash
git clone <repository-url>
cd monday-bi-agent
```

Create and activate a virtual environment:

```bash
python -m venv venv
```

Windows:

```bash
venv\Scripts\activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

## 9. Run Locally

Start the Streamlit application:

```bash
streamlit run app.py
```

The application will open in the browser.

## 10. Example Questions

```text
What is the total deal value in the Mining sector?
```

```text
How many work orders are completed?
```

```text
What is the total revenue from won deals?
```

```text
Show the billing status of work orders.
```

```text
What is the total surveyed quantity for LiDAR?
```

```text
Show the data-quality issues.
```

## 11. Data Handling and Caveats

The application is designed to handle imperfect business data. It may identify missing values, normalize inconsistent formats, and report caveats alongside results.

Outliers may be shown separately because they can materially affect totals. Results should be interpreted according to the caveats returned by the analytics functions.

If there is no shared identifier between Deals and Work Orders, item-level reconciliation may not be possible. The application should communicate this limitation instead of claiming that records have been matched.

## 12. Security Notes

Do not include the following in the repository or submission ZIP:

```text
.env
venv/
__pycache__/
API keys
Access tokens
Private credentials
```

If a credential is exposed, revoke it and generate a replacement immediately.

## 13. Deployment

The application can be deployed through Streamlit Cloud:

1. Push the project to GitHub.
2. Open Streamlit Cloud.
3. Create a new application from the GitHub repository.
4. Select the correct branch and `app.py` file.
5. Add the required secrets.
6. Deploy the application.
7. Open the public URL and test the example questions.

## 14. Limitations and Future Improvements

Possible future improvements include:

- Automated tests for analytics and cleaning.
- Retry handling for temporary API failures.
- More advanced date and sector filters.
- Charts and downloadable leadership reports.
- Source tracing for each reported figure.
- Better logging and monitoring.
- Scheduled summaries for leadership updates.
- Stronger schema validation for monday.com columns.

## 15. Purpose of Leadership Updates

The agent supports leadership updates by giving concise summaries of pipeline, revenue, sector activity, work-order progress, billing, surveyed quantity, and data-quality limitations. It is intended as a decision-support tool and should not replace formal financial or operational verification.
