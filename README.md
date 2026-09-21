# Cusp

When you let AI coding agents run autonomously, they can sometimes get stuck in loops, trying the same things over and over, or silently burning through your API budget without making any real progress. 

Cusp is a smart proxy that sits between your coding agent and LLM providers (like Ollama, Gemini, or OpenAI). Before your agent takes its next action, Cusp steps in and asks: *"Is this move actually worth the cost?"* 

By analyzing the agent's recent actions, code diffs, and test results, Cusp can instantly detect when an agent is spinning its wheels. It will intercept the call, pause the agent, or safely downgrade the request to a cheaper local model. It ensures you only pay for compute when it's actively solving your problem.

## Quick Start

We prioritize a free-first local workflow, meaning you can run Cusp completely locally without needing paid API keys.   

### 1. Clone the repo and prepare your environment

```bash
cp .env.example .env
```

### 2. Start the local services
Make sure you have Docker installed and running.  
```bash
docker compose up -d
```  
This will spin up the PostgreSQL database, Redis, the Gateway, the Bg Worker, and the React Dashboard.

### 3. Verify it's working
- Gateway Health Check: `http://localhost:8000/health`
- Dashboard: `http://localhost:5173`