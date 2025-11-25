import json
import time
import os
import sys
from typing import List, Dict, Any

import requests
from openai import OpenAI


# ==========================
# Configuration
# ==========================

# HTTP endpoint where env/minecraft_server.py is serving its Flask app
BRIDGE_BASE_URL = "http://localhost:5000"

# Default OpenAI model to use for planning
DEFAULT_MODEL = "gpt-4o-mini"  # change to "gpt-4o" or any model your key has access to

# How long to sleep between actions so you can see them in-game
ACTION_DELAY_SECONDS = 1.0


# ==========================
# OpenAI / LLM Setup
# ==========================

def load_api_key_from_file() -> str:
    """
    Load an API key from API_KEY_LIST in the current working directory.
    Tries OPENAI first, then AGENT_KEY.
    """
    path = os.path.join(os.getcwd(), "API_KEY_LIST")
    if not os.path.exists(path):
        print(f"[ERROR] API_KEY_LIST not found at {path}")
        print("Create API_KEY_LIST with at least one OpenAI key under 'OPENAI' or 'AGENT_KEY'.")
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    keys = data.get("OPENAI") or data.get("AGENT_KEY") or []
    if not keys:
        print("[ERROR] No keys found in API_KEY_LIST under 'OPENAI' or 'AGENT_KEY'.")
        sys.exit(1)

    return keys[0]


def make_openai_client() -> OpenAI:
    """
    Create an OpenAI client using the API key from API_KEY_LIST.
    """
    api_key = load_api_key_from_file()
    client = OpenAI(
        api_key=api_key,
        base_url="https://api.openai.com/v1"
    )
    return client


# ==========================
# LLM Prompting
# ==========================

SYSTEM_PROMPT = """
You control a single Minecraft agent named Alice.

The user will describe a task in natural language (for example, "walk to Tigerish and say hi").

You must output a JSON object ONLY, with no extra text, using this schema:

{
  "actions": [
    {
      "type": "move_to_player",
      "player": "<player name>"
    },
    {
      "type": "chat_to_player",
      "player": "<player name>",
      "message": "<what Alice should say in chat>"
    },
    {
      "type": "done",
      "reason": "<why the task is complete or impossible>"
    }
  ]
}

Rules:
- Only output JSON, NOTHING else.
- The top-level key must be "actions".
- "actions" must be a list of 1 to 3 actions.
- Allowed action types: "move_to_player", "chat_to_player", "done".
- Use "move_to_player" to get closer to a player by name (e.g. "Tigerish").
- Use "chat_to_player" to send a chat message to a player by name.
- Use a single "done" action when you believe the task is finished or cannot be done.
- You can mix move and chat actions before "done" if helpful, e.g. move then chat then done.
- Do not invent player names; use the names mentioned in the task.
"""


def plan_actions(client: OpenAI, model: str, task_text: str) -> Dict[str, Any]:
    """
    Use the LLM to turn the user's task into a JSON action plan.
    Returns a dict like:
    {
      "actions": [
        {"type": "move_to_player", "player": "Tigerish"},
        {"type": "chat_to_player", "player": "Tigerish", "message": "Hello!"},
        {"type": "done", "reason": "..."}
      ]
    }
    """
    user_prompt = json.dumps(
        {
            "task": task_text
        },
        ensure_ascii=False
    )

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
    )

    content = response.choices[0].message.content
    # Expecting pure JSON
    try:
        plan = json.loads(content)
        if not isinstance(plan, dict):
            raise ValueError("Top-level JSON is not an object.")
        if "actions" not in plan:
            raise ValueError("JSON has no 'actions' key.")
        if not isinstance(plan["actions"], list):
            raise ValueError("'actions' must be a list.")
        return plan
    except Exception as e:
        print("[ERROR] Failed to parse LLM response as JSON.")
        print("Raw response:")
        print(content)
        print("Parse error:", e)
        # Fallback: just say we are done and can't do it.
        return {
            "actions": [
                {
                    "type": "done",
                    "reason": "Could not parse LLM response."
                }
            ]
        }


# ==========================
# Bridge / HTTP calls
# ==========================

def post_json(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Helper to POST JSON to the bridge's HTTP API and return JSON response.
    """
    url = f"{BRIDGE_BASE_URL}{path}"
    try:
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[ERROR] Request to {url} failed:", e)
        return {"message": str(e), "status": False}


def action_move_to_player(player: str) -> None:
    """
    Ask Alice to move toward the nearest entity/player with the given name.
    This uses the /post_move_to endpoint.
    """
    print(f"[ACTION] move_to_player: {player}")
    payload = {"name": player}
    result = post_json("/post_move_to", payload)
    print(f"  -> {result.get('message')}")
    time.sleep(ACTION_DELAY_SECONDS)


def action_chat_to_player(player: str, message: str) -> None:
    """
    Ask Alice to talk to a player via /post_talk_to.
    """
    print(f"[ACTION] chat_to_player: {player} | '{message}'")
    payload = {
        "entity_name": player,
        "message": message,
        "emotion": ""
    }
    result = post_json("/post_talk_to", payload)
    print(f"  -> {result.get('message')}")
    time.sleep(ACTION_DELAY_SECONDS)


def apply_actions(actions: List[Dict[str, Any]]) -> None:
    """
    Execute the planned actions one by one by calling the bridge API.
    """
    for idx, action in enumerate(actions):
        a_type = action.get("type")
        if a_type == "move_to_player":
            player = action.get("player")
            if not player:
                print("[WARN] move_to_player action without 'player' field. Skipping.")
                continue
            action_move_to_player(player)

        elif a_type == "chat_to_player":
            player = action.get("player")
            message = action.get("message", "")
            if not player or not message:
                print("[WARN] chat_to_player action missing 'player' or 'message'. Skipping.")
                continue
            action_chat_to_player(player, message)

        elif a_type == "done":
            reason = action.get("reason", "(no reason provided)")
            print(f"[DONE] {reason}")
            # Done doesn't call the game API; it's just meta.
        else:
            print(f"[WARN] Unknown action type '{a_type}'. Skipping.")


# ==========================
# Simple REPL
# ==========================

def main():
    print("========================================")
    print(" Alice Live Controller")
    print("========================================")
    print("Prerequisites:")
    print("  1) Minecraft server running at 127.0.0.1:25565")
    print("  2) Bridge running, e.g.:")
    print('       python env\\minecraft_server.py -H "127.0.0.1" -P 25565 -LP 5000 -U "Alice" -W "world" -D False')
    print("  3) API_KEY_LIST present with an OpenAI key.")
    print("----------------------------------------")
    print("Type natural-language tasks for Alice.")
    print("Examples:")
    print('  Walk to Tigerish and say hello.')
    print('  Go talk to Tigerish and ask how they are.')
    print("Type 'quit' or 'exit' to stop.")
    print("========================================")

    client = make_openai_client()
    model = DEFAULT_MODEL

    # Quick connectivity check
    print("[INFO] Checking connection to bridge at", BRIDGE_BASE_URL)
    try:
        ping_resp = requests.get(f"{BRIDGE_BASE_URL}/post_ping", timeout=5)
        print("  -> /post_ping response:", ping_resp.text)
    except Exception as e:
        print("[WARN] Could not reach /post_ping:", e)
        print("Make sure env\\minecraft_server.py is running.")
        # We continue anyway; maybe ping isn't available.

    while True:
        try:
            task = input("\nTask for Alice> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[INFO] Exiting.")
            break

        if not task:
            continue
        if task.lower() in ("quit", "exit"):
            print("[INFO] Exiting.")
            break

        print(f"[INFO] Planning actions for task: {task!r}")
        plan = plan_actions(client, model, task)
        actions = plan.get("actions", [])
        if not actions:
            print("[WARN] LLM returned no actions.")
            continue

        print("[INFO] Executing actions:")
        apply_actions(actions)


if __name__ == "__main__":
    main()
