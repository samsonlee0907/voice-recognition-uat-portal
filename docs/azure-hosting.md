# Host the standalone portal on Azure Container Apps

These commands provision **your new development environment**, not the original
application. They incur registry, environment, compute and model-usage charges.
Use explicit subscription selection and names approved for your environment.

Prerequisites: [Foundry setup](foundry-setup.md) complete, Azure CLI with
Container Apps/Bicep support, provider registrations, and permission to create
hosting resources and assign ACR/AI roles. The supplied registry must be in the
deployment resource group and use the ordinary ACR RBAC mode.

## 1. Choose names and build the image

From the repository root in PowerShell:

```powershell
$subscription = '<your-subscription-id>'
$group = '<your-hosting-resource-group>'
$registry = '<globally-unique-alphanumeric-acr-name>'
$app = '<your-container-app-name>'
$voiceEndpoint = 'https://<your-foundry-resource>.services.ai.azure.com'
$liveEndpoint = 'https://<your-gpt-live-resource>.openai.azure.com'
$liveDeployment = 'gpt-live-1'
$version = 'recognition-uat-20261104-r1'

az group create --subscription $subscription --name $group --location eastus2
az acr create --subscription $subscription --resource-group $group --name $registry --sku Basic
az acr build --subscription $subscription --registry $registry `
  --image "recognition-uat:$version" --file Dockerfile .
$server = az acr show --subscription $subscription --name $registry --query loginServer -o tsv
$digest = az acr repository show --subscription $subscription --name $registry `
  --image "recognition-uat:$version" --query digest -o tsv
$image = "$server/recognition-uat@$digest"
```

Confirm each command succeeded before continuing. Pinning the digest prevents a
later tag overwrite from silently changing the deployed code.

## 2. Generate NEW login credentials

```powershell
.\.venv\Scripts\python.exe tools\make_credentials.py --username developer
```

The tool prompts twice for a new developer password and writes its hash and a new
session secret to `.local\credentials.parameters.json`. The plaintext password is
not written. The directory is excluded from Git and Docker build context. It still
contains a **secret signing key**: restrict filesystem permissions, never paste it
into an issue/chat, and remove this specific file after storing secrets appropriately.
Do not reuse the original portal's password or session secret.

## 3. Deploy hosting

```powershell
az deployment group create --subscription $subscription --resource-group $group `
  --name recognition-uat-hosting --template-file infra\hosting.bicep `
  --parameters '@.local\credentials.parameters.json' `
  --parameters appName=$app registryName=$registry containerImage=$image `
    voiceLiveEndpoint=$voiceEndpoint gptLiveEndpoint=$liveEndpoint `
    gptLiveDeployment=$liveDeployment appVersion=$version

$principal = az deployment group show --subscription $subscription --resource-group $group `
  --name recognition-uat-hosting --query properties.outputs.principalId.value -o tsv
$url = az deployment group show --subscription $subscription --resource-group $group `
  --name recognition-uat-hosting --query properties.outputs.url.value -o tsv
```

The template creates a user-assigned managed identity and ACR pull role before the
app, a managed environment and an HTTPS-only Container App. Login values become
Container App secrets with secret-reference environment variables. There are no
Azure API keys in the browser or image. It sets 0-1 replicas because the demo's
admission limits are process-local, not a distributed limiter.

ACR/identity role propagation can delay the initial image pull. Inspect revision
health and retry once permissions have propagated rather than enabling registry
admin credentials or replacing a failed image with a success-looking placeholder.

The template does **not** assign AI access across arbitrary resource groups or
subscriptions. An administrator must grant `$principal` the data-plane roles from
the [Foundry checklist](foundry-setup.md#4-authorize-the-runtime-identity) on the
resource(s) behind the two endpoints. Runtime role assignment is separate from
the deployment operator's own access.

## 4. Smoke the actual URL

Open `$url/recognition/uat`, sign in, enable, consent and test the microphone.
Verify all three real lanes, a processed answer and clean stop. Check that unauthenticated
page access redirects, unauthenticated API access returns 401, and no audio starts
before the tester explicitly starts.

`$url/health` is public and deliberately does not claim model readiness.
If you see a failed lane, inspect provider configuration, model access, RBAC,
networking and browser WebRTC—not just revision health.

Do not enable `ALLOW_INSECURE_LOCALHOST` in Container Apps. Use private-network
hosting where your organization requires it; the server must be able to reach
the approved AI endpoints and the browser must have an approved media path.

## Updating and operating

Rebuild with a new version/tag, capture its digest, and update the app image and
`APP_VERSION` together. For a shared environment, stage a zero-traffic revision,
qualify it, then promote traffic; preserve the previous revision until verification.
Changing secrets can invalidate sessions and should follow a deliberate rotation.

Browser-local UAT records are not a database or backup. Export approved synthetic
records before clearing storage. Logs should contain provider error codes, not
customer audio, transcripts, passwords, bearer tokens or SDP bodies.

The Bicep files are development templates, not a claim that private networking,
production Entra login, centralized abuse controls, monitoring retention or a
real call-center telephony integration has been implemented.
