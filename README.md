# 🎭 Artificial Social Environments: Human Perception of AI Agents in Minecraft

<img width="1650" height="400" alt="specbanner1650400" src="https://github.com/user-attachments/assets/72d313d8-e75e-4e61-9f0c-e098ccd506a8" />


<p align="center">
    <img src='https://img.shields.io/badge/Minecraft-1.19.2-green?style=for-the-badge&logo=minecraft' alt='Minecraft'>
    <img src='https://img.shields.io/badge/Python-3.8+-blue?style=for-the-badge&logo=python' alt='Python'>
    <img src='https://img.shields.io/badge/Agents-Voyager%20Based-orange?style=for-the-badge' alt='Agents'>
</p>



## Overview

Can humans tell when they are the only real person in a digital world? 

This project investigates the **"Truman Show" effect** in Minecraft. We created a controlled Survival Multiplayer (SMP) server populated entirely by AI agents to test if human players can detect the artificial nature of their social environment. By integrating **Large Language Models (LLMs)** with **embodied agents**, we simulate a living, breathing server where agents build, craft, chat, and react to suspicion.

<img width="3616" height="1184" alt="spec_final_workflow_whitebg" src="https://github.com/user-attachments/assets/fb3b99c5-f4ec-4a4e-a36b-b32f361b3574" />

---

## 🧠 System Architecture

Our framework coordinates complex task dependencies and social interactions through four main components:

1.  **Modified Voyager Agents:** Embodied agents capable of open-ended exploration and skill acquisition.
2.  **Social Behavior Layer:**
    * **Personas:** Distinct personality traits (e.g., "Risk-taking Explorer," "Cautious Builder").
    * **Emotional Mimicry:** Sentiment analysis to react appropriately to player deaths or achievements (e.g., typing "RIP" or "gg").
    * **Suspicion Handling:** Specialized prompts to deflect accusations of being a "bot" via humor or topic shifting.
3.  **Server Plugin:** A custom Java plugin that logs events (proximity, interactions) and routes chat to the agent backend.
4.  **Multi-Agent Controller:** Orchestrates the group to ensure the server feels populated and active.

---

## 📊 Key Findings

In our pilot study with ~10 participants:
* **Detection Failure:** Only **2/10** participants correctly identified that *all* other players were AI.
* **The "Mixed" Illusion:** Most players believed they were in a mixed server of humans and bots.
* **Behavioral Cues:** "Pathfinding glitches" triggered suspicion, while **emotional responsiveness** (reacting to chat/events) significantly increased perceived realism.

---

## 🛠️ Setup and Configuration

### Requirements
* **Python 3.8+**
* **Node.js & npm** (for the Mineflayer interface)
* **Minecraft Java Edition 1.19.2**
* **API Keys:** OpenAI (GPT-4 recommended for best social improvisation).

### Installation Steps

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/seanhlewis/mcagentgym.git
    cd artificial-social-env
    ```

2.  **Set up Virtual Environment:**
    ```bash
    python -m venv venv
    source venv/bin/activate  # Windows: venv\Scripts\activate
    pip install -r requirements.txt
    ```

3.  **Install Node Dependencies:**
    ```bash
    npm install
    ```

4.  **Configure Keys:**
    Create a `keys.json` file in the root directory:
    ```json
    {
       "OPENAI_API_KEY": "your_key_here"
    }
    ```

---

## 🎮 Minecraft Server Setup (1.19.2)

To run the experiment, you must host a local Minecraft server.

### 1. Preparation
* Ensure **Java 17+** is installed (`java -version`).
* Download the **server jar** for 1.19.2.

### 2. Configuration
1.  Create a folder named `server` and place the `.jar` file inside.
2.  Run the server once to generate files:
    ```bash
    java -Xmx4G -Xms4G -jar server.jar nogui
    ```
3.  Open `eula.txt` and set `eula=true`.
4.  **Crucial Step:** Open `server.properties` and ensure the following:
    * `online-mode=false` (If testing locally without auth).
    * `difficulty=normal`
    * `gamemode=survival`
    * `server-port=25565`

### 3. Running the Simulation
1.  Start the Minecraft Server.
2.  In a separate terminal, launch the Agent Controller:
    ```bash
    python main.py
    ```
3.  Join the server via Minecraft Client at `localhost:25565`.

---

## 🤝 Contribution & License

Contributions are welcome! Please focus on improving the **suspicion detection logic** or adding **long-term memory** modules.

This project is licensed under the [MIT License](LICENSE).

---

### Acknowledgements

This project builds upon the incredible work of the open-source community.
* **Base Framework:** Adapted from **VillagerBench** for multi-agent coordination.
* **Agent Logic:** Built using **Voyager** and **MineDojo** for embodied control and skill learning.

<p align="center">
  <sub style="color: gray">Jiayu Zhang & Sean Hardesty Lewis • Final Report</sub>
</p>
