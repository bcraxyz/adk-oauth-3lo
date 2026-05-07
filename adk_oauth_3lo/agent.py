from __future__ import annotations

import os
import httpx
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.auth.auth_credential import AuthCredential
from google.adk.auth.auth_tool import AuthConfig
from google.adk.auth.credential_manager import CredentialManager
from google.adk.integrations.agent_identity import GcpAuthProvider, GcpAuthProviderScheme
from google.adk.tools.authenticated_function_tool import AuthenticatedFunctionTool

from dotenv import load_dotenv
load_dotenv()

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION")
GITHUB_3LO_AUTH_PROVIDER_ID = os.environ.get("GITHUB_3LO_AUTH_PROVIDER_ID")

GITHUB_3LO_AUTH_PROVIDER = (
    f"projects/{PROJECT_ID}/locations/{LOCATION}/connectors/{GITHUB_3LO_AUTH_PROVIDER_ID}"
)

CONTINUE_URI = os.environ.get("CONTINUE_URI", "http://localhost:8080/commit")

CredentialManager.register_auth_provider(GcpAuthProvider())


async def github_list_repos(credential: AuthCredential) -> str | list:
    """Lists the authenticated user's GitHub repositories."""
    token = None
    if credential.http and credential.http.credentials:
        token = credential.http.credentials.token

    if not token:
        return "Error: No authentication token available."

    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://api.github.com/user/repos",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            params={"sort": "updated", "per_page": 10},
        )

        if response.status_code != 200:
            return f"Error from GitHub API: {response.status_code} - {response.text}"

        repos = response.json()
        return [
            {
                "name": r.get("full_name"),
                "private": r.get("private"),
                "description": r.get("description"),
                "stars": r.get("stargazers_count"),
            }
            for r in repos
        ]


github_auth_config = AuthConfig(
    auth_scheme=GcpAuthProviderScheme(
        name=GITHUB_3LO_AUTH_PROVIDER,
        scopes=["repo"],
        continue_uri=CONTINUE_URI,
    )
)

github_list_repos_tool = AuthenticatedFunctionTool(
    func=github_list_repos,
    auth_config=github_auth_config,
)

root_agent = Agent(
    name="adk_oauth_3lo",
    model="gemini-2.5-flash",
    instruction=(
        "You are a GitHub assistant. Use your tool to list the user's "
        "GitHub repositories. Keep responses concise."
    ),
    tools=[github_list_repos_tool],
)

app = App(
    name="adk_oauth_3lo",
    root_agent=root_agent,
)
