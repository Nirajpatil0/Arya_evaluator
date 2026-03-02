# 🤖 ARYA Session Evaluator

A web application that automatically evaluates ARYA chatbot sessions using Gemini AI as an LLM judge.

---

## 📁 Folder & File Structure

```
arya_evaluator/
│
├── app.py                  ← Streamlit web app (main entry point)
├── excel_processor.py      ← Reads Excel, groups sessions, writes results back
├── gemini_evaluator.py     ← Drives Gemini chat, parses output
│
├── requirements.txt        ← All Python dependencies
├── .env.example            ← Template for your API key (copy → .env)
├── .env                    ← YOUR actual API key (never commit this!)
├── .gitignore              ← Keeps .env and output files out of git
│
└── README.md               ← This file
```

---

## ⚙️ Setup Instructions (Step-by-Step)

### Step 1 — Install Python
Make sure Python 3.9 or higher is installed.
```bash
python --version
```

### Step 2 — Create a Virtual Environment
A virtual environment keeps your project dependencies isolated.

**On Windows:**
```bash
cd arya_evaluator
python -m venv venv
venv\Scripts\activate
```

**On Mac/Linux:**
```bash
cd arya_evaluator
python3 -m venv venv
source venv/bin/activate
```

You will see `(venv)` in your terminal — this means the environment is active.

### Step 3 — Install Dependencies
```bash
pip install -r requirements.txt
```

This installs:
| Library | Purpose |
|---|---|
| `streamlit` | Web interface |
| `pandas` | Reading Excel, grouping sessions |
| `openpyxl` | Writing back to Excel preserving formatting |
| `google-generativeai` | Gemini API SDK |
| `python-dotenv` | Loading API key from .env file |

### Step 4 — Get Your Gemini API Key
1. Go to https://aistudio.google.com/app/apikey
2. Click "Create API Key"
3. Copy the key

### Step 5 — Configure Your API Key
Copy the example env file and fill in your key:

**On Windows:**
```bash
copy .env.example .env
```
**On Mac/Linux:**
```bash
cp .env.example .env
```

Open `.env` and replace `your_gemini_api_key_here` with your actual key:
```
GEMINI_API_KEY=AIzaSy...your_actual_key_here...
```

> ⚠️ Never share or commit the `.env` file. It is listed in `.gitignore`.

### Step 6 — Run the App
```bash
streamlit run app.py
```

The app will open automatically in your browser at `http://localhost:8501`

---

## 🖥️ How to Use the App

1. **Upload** your Excel file (`.xlsx`) using the file uploader
2. **Preview** the data and session summary to confirm it loaded correctly
3. **Enter your Gemini API key** in the sidebar (if not set in `.env`)
4. Click **▶ Start Evaluation**
5. Watch real-time logs and progress bar as each session is evaluated
6. **Download** the completed Excel file with results filled in

---

## 📊 Excel Column Reference

| Column | What it contains |
|---|---|
| `SESSION_ID` | Groups rows into sessions |
| `QUERY` | The user query sent to ARYA |
| `RESPONSE` | ARYA's response |
| `LLM GCR` | **Written by tool** — Full Gemini evaluation text |
| `LLM GCR yes/no` | **Written by tool** — "Yes" or "No" (colour coded) |

---

## 🔧 Key Design Decisions

### Session Isolation
Each session gets a **fresh Gemini chat instance**. There is zero context bleed between sessions. This is critical for accurate evaluation.

### Rate Limit Handling
- **Retry logic**: Up to 3 retries per API call with exponential back-off (10s, 20s, 30s)
- **Inter-session delay**: 3 seconds between sessions as a courtesy buffer
- If you hit persistent quota errors, increase `SESSION_WAIT_SEC` in `gemini_evaluator.py`

### Token Limit Safety
- A warning is logged if any session exceeds 30 turns (configurable via `MAX_TURNS_WARN`)
- Very large sessions risk hitting Gemini's context window; Gemini 1.5 Flash has a 1M token window so this is rarely a problem in practice

### Parse Error Safety
If Gemini returns an unexpected format:
- The raw text is still saved to `LLM GCR`
- `LLM GCR yes/no` is set to `ERROR` (highlighted orange in Excel)
- The app continues evaluating remaining sessions without crashing

### Excel Formatting Preserved
`openpyxl` is used for writing (not pandas `.to_excel()`) so original cell colours, fonts, borders, and column widths are preserved. Only the two output columns are touched.

---

## 🐛 Troubleshooting

| Problem | Fix |
|---|---|
| `ModuleNotFoundError` | Run `pip install -r requirements.txt` inside the venv |
| `GEMINI_API_KEY not set` | Check your `.env` file or enter key in sidebar |
| `429 quota exceeded` | Increase `SESSION_WAIT_SEC` in `gemini_evaluator.py` |
| `Column not found` error | Ensure Excel has exactly these headers: `SESSION_ID`, `QUERY`, `RESPONSE`, `LLM GCR`, `LLM GCR yes/no` |
| App freezes | Check terminal for error logs; Streamlit re-runs on state changes |

---

## 🔄 Switching Gemini Model

In `gemini_evaluator.py`, change the model name in `_build_model()`:

```python
model_name="gemini-1.5-flash"    # Faster, cheaper, 1M context
model_name="gemini-1.5-pro"      # Smarter, slower, better reasoning
model_name="gemini-2.0-flash"    # Latest fast model (if available)
```
