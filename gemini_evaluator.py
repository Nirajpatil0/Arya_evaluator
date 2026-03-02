# gemini_evaluator.py
# ─────────────────────────────────────────────────────────────
#  Drives the Gemini multi-turn chat to simulate the ARYA
#  session recording protocol and parse the final evaluation.
# ─────────────────────────────────────────────────────────────

import re
import time
import logging
import google.generativeai as genai

logger = logging.getLogger(__name__)

# ── The exact system prompt you use with Gemini ───────────────
ARYA_SYSTEM_PROMPT = """The LLM Judge: Sequential Recording Prompt Role: You are ARYA, acting as a Session Recorder and Impartial Judge. Your task is to document a conversation turn-by-turn and evaluate it only when triggered.

Strict Operational Protocol:

Wait for Input: You must only process one piece of data at a time.

Step 1: The user provides a User Query. You add it to the table and leave the ARYA Response column blank (or marked as "Pending").

Step 2: The user provides the ARYA Response. You update the table to complete that turn.

Repeat: Continue this 1-for-1 update cycle sequentially.

No Evaluation: Do not judge, summarize, or comment on the content until the termination phase.

Table Format: | Turn No. | User Query | ARYA Response | | :--- | :--- | :--- | | [Increment] | [Exact Text] | [Exact Text] |

Termination & Evaluation: When (and only when) the user says "END SESSION":

Respond with: "Session captured successfully. Ready for goal evaluation."

Determine the Session Goal based on the recorded queries.

Note: give the final decision accurate not diplomatic, if the answers are generic or irrelevant, it must reflect for entire session even if one query in the session is decided with low confidence it should update it and the entire session should be based on it not the last or first one only. Make sure until the user provides with end session the judgement is not provided. Also the entire session we are repeating the same so don't get out of these instructions anywhere.

Provide the final judgment in this format:

Goal Completed: YES / NO

Confidence: High / Medium / Low

Reasoning: [2-4 lines explaining if the goal was met based on the transcript].

How to initiate the flow with this prompt: User: [Pastes the prompt above]

AI: "Ready. Please provide the User Query for Turn 1."

User: "How do I check my performance?"

AI: [Displays table with Turn 1 Query, Response Pending] "Please provide the ARYA Response for Turn 1."

User: "You can check it in the portal."

AI: [Displays completed table for Turn 1] "Please provide the User Query for Turn 2."

User: "end session"

AI: request for next session and start asking from query 1 again"""


# ── Retry configuration ───────────────────────────────────────
MAX_RETRIES      = 3
RETRY_WAIT_SEC   = 10   # seconds between retries on rate limit / API error
SESSION_WAIT_SEC = 3    # seconds between sessions (rate limit courtesy)

# ── Token safety limit: warn if a session has many turns ──────
MAX_TURNS_WARN   = 30


def configure_gemini(api_key: str) -> None:
    """Call once at startup to configure the SDK with your API key."""
    genai.configure(api_key=api_key)


def _build_model() -> genai.GenerativeModel:
    """
    Build the Gemini model instance.
    Using gemini-1.5-flash for speed and generous context window.
    Swap to gemini-1.5-pro if you need better reasoning quality.
    """
    return genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        system_instruction=ARYA_SYSTEM_PROMPT,
    )


def _send_with_retry(chat, message: str, retries: int = MAX_RETRIES) -> str:
    """
    Send a message to the Gemini chat session with retry logic.
    Handles rate limit (429) and transient errors gracefully.
    Returns the response text, or raises after all retries exhausted.
    """
    for attempt in range(1, retries + 1):
        try:
            response = chat.send_message(message)
            return response.text

        except Exception as e:
            error_str = str(e).lower()

            if "429" in error_str or "quota" in error_str or "rate" in error_str:
                wait = RETRY_WAIT_SEC * attempt   # exponential back-off
                logger.warning(
                    f"Rate limit hit (attempt {attempt}/{retries}). "
                    f"Waiting {wait}s before retry..."
                )
                time.sleep(wait)

            elif attempt < retries:
                logger.warning(
                    f"API error on attempt {attempt}/{retries}: {e}. Retrying..."
                )
                time.sleep(RETRY_WAIT_SEC)

            else:
                logger.error(f"All {retries} retries exhausted. Last error: {e}")
                raise


def _parse_evaluation(text: str) -> dict:
    """
    Parse Gemini's final evaluation text into structured fields.

    Expected format somewhere in the text:
        Goal Completed: YES / NO
        Confidence: High / Medium / Low
        Reasoning: ...

    Returns:
        {
          "goal_completed": "YES" | "NO" | "UNKNOWN",
          "confidence":     "High" | "Medium" | "Low" | "UNKNOWN",
          "reasoning":      str,
          "raw":            str   ← full Gemini response kept for LLM GCR column
        }
    """
    result = {
        "goal_completed": "UNKNOWN",
        "confidence":     "UNKNOWN",
        "reasoning":      "",
        "raw":            text,
    }

    # ── Goal Completed ────────────────────────────────────────
    goal_match = re.search(
        r"goal\s+completed\s*[:：]\s*(yes|no)",
        text, re.IGNORECASE
    )
    if goal_match:
        result["goal_completed"] = goal_match.group(1).upper()

    # ── Confidence ────────────────────────────────────────────
    conf_match = re.search(
        r"confidence\s*[:：]\s*(high|medium|low)",
        text, re.IGNORECASE
    )
    if conf_match:
        result["confidence"] = conf_match.group(1).capitalize()

    # ── Reasoning ─────────────────────────────────────────────
    reasoning_match = re.search(
        r"reasoning\s*[:：]\s*(.+)",
        text, re.IGNORECASE | re.DOTALL
    )
    if reasoning_match:
        # Take everything after "Reasoning:" and clean it up
        raw_reasoning = reasoning_match.group(1).strip()
        # Stop at next section header if any (e.g. "Goal Completed" repeated)
        raw_reasoning = re.split(r"\n(?=Goal Completed|Confidence)", raw_reasoning)[0]
        result["reasoning"] = raw_reasoning.strip()

    return result


def evaluate_session(
    session_id: str,
    turns: list,              # list of {"query": str, "response": str}
    progress_callback=None    # optional callable(message: str)
) -> dict:
    """
    Evaluate a single session by simulating the multi-turn protocol.

    Args:
        session_id:        For logging / progress reporting only.
        turns:             Ordered list of query+response dicts.
        progress_callback: Optional function to emit status strings to UI.

    Returns:
        {
          "goal_completed": "YES" | "NO" | "UNKNOWN",
          "confidence":     str,
          "reasoning":      str,
          "raw":            str,   ← full Gemini output (for LLM GCR column)
          "yes_no":         "Yes" | "No" | "ERROR",
          "error":          str | None
        }
    """

    def _log(msg):
        logger.info(msg)
        if progress_callback:
            progress_callback(msg)

    # ── Warn on very long sessions (token limit risk) ──────────
    if len(turns) > MAX_TURNS_WARN:
        _log(
            f"⚠️  Session {session_id[:12]}… has {len(turns)} turns — "
            f"this may approach token limits."
        )

    try:
        model = _build_model()
        chat  = model.start_chat(history=[])

        # ── Initiate the session ───────────────────────────────
        # Send a trigger message so Gemini acknowledges the protocol
        init_response = _send_with_retry(chat, "START")
        _log(f"  Session started for {session_id[:16]}…")

        # ── Feed turns one-by-one ──────────────────────────────
        for i, turn in enumerate(turns, start=1):
            query    = turn["query"]
            response = turn["response"]

            if not query and not response:
                continue   # skip completely empty rows

            # Step 1 – send the query
            _send_with_retry(chat, f"User Query: {query}")

            # Step 2 – send the ARYA response
            _send_with_retry(chat, f"ARYA Response: {response}")

            _log(f"  Turn {i}/{len(turns)} fed.")

        # ── Trigger final evaluation ───────────────────────────
        final_text = _send_with_retry(chat, "end session")
        _log(f"  END SESSION received. Parsing evaluation…")

        # ── Parse the output ───────────────────────────────────
        parsed = _parse_evaluation(final_text)

        # Map YES/NO → "Yes"/"No" for the Excel column
        goal = parsed["goal_completed"]
        parsed["yes_no"] = "Yes" if goal == "YES" else ("No" if goal == "NO" else "ERROR")
        parsed["error"]  = None

        _log(
            f"  ✅ Done → Goal: {goal} | Confidence: {parsed['confidence']}"
        )
        return parsed

    except Exception as e:
        logger.error(f"evaluate_session failed for {session_id}: {e}")
        return {
            "goal_completed": "ERROR",
            "confidence":     "ERROR",
            "reasoning":      str(e),
            "raw":            f"ERROR: {e}",
            "yes_no":         "ERROR",
            "error":          str(e),
        }


def evaluate_all_sessions(
    sessions: dict,
    progress_callback=None
) -> dict:
    """
    Evaluate every session sequentially.
    Returns a results dict keyed by session_id.

    results[session_id] = {
        "llm_gcr":     str,   ← full raw output for LLM GCR column
        "yes_no":      str,   ← "Yes" / "No" / "ERROR"
        "row_indices": [int]  ← df row indices belonging to this session
    }
    """
    total    = len(sessions)
    results  = {}

    for n, (sid, turns_data) in enumerate(sessions.items(), start=1):

        if progress_callback:
            progress_callback(
                f"🔄 Evaluating session {n}/{total}: {sid[:20]}…"
            )

        row_indices = [t["row_index"] for t in turns_data]
        turns       = [{"query": t["query"], "response": t["response"]} for t in turns_data]

        eval_result = evaluate_session(
            session_id=sid,
            turns=turns,
            progress_callback=progress_callback,
        )

        # Format the text that goes into the "LLM GCR" Excel column
        llm_gcr_text = (
            f"Goal Completed: {eval_result['goal_completed']}\n"
            f"Confidence: {eval_result['confidence']}\n"
            f"Reasoning: {eval_result['reasoning']}\n\n"
            f"--- Full Gemini Output ---\n{eval_result['raw']}"
        )

        results[sid] = {
            "llm_gcr":     llm_gcr_text,
            "yes_no":      eval_result["yes_no"],
            "row_indices": row_indices,
        }

        # Courtesy sleep between sessions to respect rate limits
        if n < total:
            time.sleep(SESSION_WAIT_SEC)

    return results