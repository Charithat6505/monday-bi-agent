# Skylark BI Agent

A conversational business-intelligence agent that answers founder-level questions by querying
two monday.com boards (Work Orders and Deals) live through the monday.com GraphQL API.

> Work in progress. The full README (architecture, assumptions, trade-offs, AI tools used,
> challenges, improvements) is completed at the final step.

## Run locally

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then fill in MONDAY_API_TOKEN and GEMINI_API_KEY
python check_monday.py      # smoke test the monday.com connection
streamlit run app.py
```

## monday.com configuration

1. Create two boards by importing the provided Excel files (Import data -> Excel):
   - **Work Orders**: header row = row 2, item name column = `Serial #`
   - **Deals**: header row = row 1, item name column = `Deal Name`
2. Import rows as-is (duplicates and messy values kept on purpose; cleaning happens in the agent).
3. Generate a personal API token (profile picture -> Developers -> My Access Tokens) and put it in
   `MONDAY_API_TOKEN`. The app only ever sends read-only GraphQL queries.

## Tests

```bash
python -m unittest discover -s tests -v
```
