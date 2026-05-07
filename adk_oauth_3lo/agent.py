from __future__ import annotations

import os
import httpx
from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.apps import App, ResumabilityConfig
from google.adk.auth.auth_credential import AuthCredential
from google.adk.auth.auth_tool import AuthConfig
from google.adk.auth.credential_manager import CredentialManager
from google.adk.integrations.agent_identity import GcpAuthProvider, GcpAuthProviderScheme
from google.adk.tools.authenticated_function_tool import AuthenticatedFunctionTool

load_dotenv()

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION")
MSGRAPH_3LO_AUTH_PROVIDER_ID = os.environ.get("MSGRAPH_3LO_AUTH_PROVIDER_ID")
CONTINUE_URI = os.environ.get("CONTINUE_URI", "http://localhost:8080/commit")

MSGRAPH_3LO_AUTH_PROVIDER = (
    f"projects/{PROJECT_ID}/locations/{LOCATION}/connectors/{MSGRAPH_3LO_AUTH_PROVIDER_ID}"
)

CredentialManager.register_auth_provider(GcpAuthProvider())

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

async def msgraph_get_my_profile(credential: AuthCredential) -> str | dict:
    """Gets the signed-in user's Microsoft 365 profile."""
    token = None
    if credential.http and credential.http.credentials:
        token = credential.http.credentials.token

    if not token:
        return "Error: No authentication token available."

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{GRAPH_BASE}/me",
            headers={"Authorization": f"Bearer {token}"},
            params={"$select": "id,displayName,userPrincipalName,jobTitle,department"},
        )

        if response.status_code != 200:
            return f"Error from Microsoft Graph: {response.status_code} - {response.text}"

        return response.json()

msgraph_3lo_tool = AuthenticatedFunctionTool(
    func=msgraph_get_my_profile,
    auth_config=AuthConfig(
        auth_scheme=GcpAuthProviderScheme(
            name=MSGRAPH_3LO_AUTH_PROVIDER,
            scopes=["User.Read", "offline_access"],
            continue_uri=CONTINUE_URI,
        )
    ),
)

root_agent = Agent(
    name="adk_oauth_3lo",
    model="gemini-2.5-flash",
    instruction=(
        "You are a Microsoft 365 assistant. Use your tool to fetch the "
        "signed-in user's profile from Microsoft Graph. Keep responses concise."
    ),
    tools=[msgraph_3lo_tool],
)

app = App(
    name="adk_oauth_3lo",
    root_agent=root_agent,
    resumability_config=ResumabilityConfig(is_resumable=True),
)
