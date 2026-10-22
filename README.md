\# ActionBridge AI



\*\*ActionBridge AI\*\* is an AI-powered planning assistant designed to help students manage commitments, organize their plans, identify scheduling conflicts, and review proposed changes before applying them.



The project is being developed for the \*\*Amazon Alexa+ Hackathon\*\*.



\## Features



\- \*\*Conversational planning:\*\* Interact with the assistant through a chat API.

\- \*\*Commitment management:\*\* Retrieve and manage academic commitments.

\- \*\*Daily planning:\*\* Generate and retrieve a structured plan.

\- \*\*Conflict detection:\*\* Identify scheduling conflicts.

\- \*\*Confirmation-based changes:\*\* Review proposed changes before applying or cancelling them.

\- \*\*Persistent storage:\*\* Store application data in SQLite.

\- \*\*AI integration:\*\* Use Amazon Bedrock's Converse API with tool calling through a provider abstraction and an MCP tool server.



\## Technology Stack



| Component | Technology |

|---|---|

| Backend API | FastAPI |

| AI model integration | Amazon Bedrock Converse API |

| AI tool integration | Python MCP SDK |

| Database | SQLite |

| AWS SDK | Boto3 |

| Frontend | React, Vite, TypeScript, Tailwind CSS — planned project stack |



\## Project Structure



```text

ActionBridge-AI/

├── backend/

│   ├── main.py

│   ├── agent.py

│   ├── mcp\_server.py

│   ├── services.py

│   ├── llm\_provider.py

│   ├── seed.py

│   ├── requirements.txt

│   ├── .env.example

│   └── tests

├── frontend/

├── docs/

├── README.md

├── FRICTION\_LOG.md

└── .gitignore

```



\## Prerequisites



\- Python 3.10 or newer is recommended; the project has been tested in the current development environment with Python 3.13.

\- AWS credentials configured through a supported Boto3 credential provider.

\- Access to an Amazon Bedrock model that supports tool use, for AI-powered chat.

\- Node.js and npm will be needed when the frontend is implemented.



\## Backend Setup



\### 1. Clone the repository



```bash

git clone https://github.com/Soha-025/ActionBridge-AI.git

cd ActionBridge-AI

```



\### 2. Install Python dependencies



```powershell

cd backend

python -m pip install -r requirements.txt

```



\### 3. Configure environment variables



Create a local `.env` file from the example:



```powershell

Copy-Item .env.example .env

```



Configure the following variables in `backend/.env`:



```dotenv

AWS\_REGION=us-east-1

BEDROCK\_MODEL\_ID=your-enabled-bedrock-model-id

DEMO\_TODAY=2026-10-26

DB\_PATH=actionbridge.db

```



Replace the model ID placeholder with the ID of a tool-capable model enabled in your AWS Bedrock account.



Configure AWS credentials separately using a supported Boto3 credential provider. Never commit `.env` or expose AWS credentials in source code.



`DEMO\_TODAY` controls the reference date used by the demo. `DB\_PATH` selects the SQLite database file.



\### 4. Start the backend



From the `backend` directory:



```powershell

python -m uvicorn main:app --reload

```



The API will be available at:



\- API base URL: `http://127.0.0.1:8000`

\- Interactive API documentation: `http://127.0.0.1:8000/docs`



Keep the terminal running while testing the API.



\## Demo Data



The seed script creates three sample academic commitments:



\- Statistics quiz — Thursday

\- AI assignment — Friday

\- Presentation — Saturday



To use the fixed demo date and an isolated test database in PowerShell:



```powershell

$env:DEMO\_TODAY = "2026-10-26"

$env:DB\_PATH = ".\\actionbridge\_test.db"

python seed.py

```



Run the command again to verify that existing demo records are detected instead of duplicated.



The isolated database is useful for testing without modifying the default application database.



\## API Endpoints



| Method | Endpoint | Purpose |

|---|---|---|

| POST | `/api/chat` | Send a message to the assistant |

| GET | `/api/commitments` | Retrieve commitments |

| GET | `/api/plan` | Retrieve the current plan |

| GET | `/api/conflicts` | Retrieve scheduling conflicts |

| GET | `/api/changes/{change\_id}` | Retrieve details of a proposed change |

| POST | `/api/changes/{change\_id}/apply` | Apply a proposed change |

| POST | `/api/changes/{change\_id}/cancel` | Cancel a proposed change |



For request and response schemas, open `/docs` while the backend is running.



\## Running Tests



From the `backend` directory:



```powershell

python -m pytest -q

```



The current development checkout has passed \*\*109 tests\*\*, with one non-fatal deprecation warning. Run the tests again after making changes.



\## Architecture Overview



1\. The frontend communicates with the FastAPI backend over REST.

2\. The backend coordinates chat requests through the ActionBridge agent.

3\. The agent uses the configured language model provider to communicate with Amazon Bedrock.

4\. The MCP tool server exposes application tools for commitment management, planning, conflict detection, and proposed changes.

5\. Python services handle application logic and SQLite persistence.

6\. Proposed changes require an explicit confirmation action through the apply endpoint.



\## Development Status



The backend tests, API startup, and deterministic demo seeding have been verified in the current development environment.



The frontend and end-to-end integration still require verification in the shared checkout. Amazon Bedrock chat functionality also requires valid AWS credentials and access to the configured model.



\## Security



\- Keep `.env`, AWS credentials, and other secrets out of version control.

\- Use `.env.example` to document variable names without including real credentials.

\- Use a separate database when testing demo data.

\- Review and test proposed changes before applying them.



\## Team and Contributions



ActionBridge AI is being developed collaboratively for the Amazon Alexa+ Hackathon. Contributions include backend and AI integration, frontend development, planning logic, testing, documentation, and demo preparation.
