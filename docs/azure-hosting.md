# Host the standalone portal on Azure Container Apps

These commands provision **your new development environment**, not the original
application. They incur registry, environment, compute and model-usage charges.
Use explicit subscription selection and names approved for your environment.

## Before you start

Complete [source/tool setup](../README.md#get-the-source-and-tools) first, including
the Python virtual environment, requirements, `az login` and `$subscription`.
You do not need to start the local server. Then complete [Foundry setup](foundry-setup.md):
record the two endpoints, actual GPT-Live deployment name and full AI resource
IDs/subscriptions. This guide hosts the app; it does not deploy GPT-Live or obtain
model access/quota.

The deployment operator needs permission to create hosting resources and assign
ACR/AI roles; Contributor alone does not grant role-assignment permission. Ask
an administrator to perform any steps your account cannot authorize. The app
identity's inference access is separate from the operator's permissions.

Use approved, unused resource names. The supplied registry must be in the hosting
resource group and use ordinary ACR RBAC mode, not ABAC repository permissions.
An existing environment must be in the same hosting resource group and region.
This managed-identity path assumes the AI resources trust the same Entra tenant;
they may be in different subscriptions with appropriately scoped role assignments.

The portal has **no default passcode**. In step 3 you will **choose** its password.
Save it in an approved password manager; neither Azure nor this repository can
recover its original plaintext from the stored hash.

## 1. Prepare Azure CLI

In the same PowerShell window, from the repository root:

```powershell
az extension add --name containerapp --upgrade
az bicep install
az provider register --subscription $subscription --namespace Microsoft.App --wait
az provider register --subscription $subscription --namespace Microsoft.OperationalInsights --wait
az provider register --subscription $subscription --namespace Microsoft.ContainerRegistry --wait
az provider register --subscription $subscription --namespace Microsoft.ManagedIdentity --wait
```

Confirm every command succeeds; registrations can take several minutes. Only
register providers needed for resources you are approved to create. Foundry
provisioning additionally needs `Microsoft.CognitiveServices`, as described in
its setup guide.

## 2. Choose names and build the image

From the repository root in PowerShell:

```powershell
$location = 'eastus2'
$group = '<new-hosting-resource-group>'
$registry = '<globally-unique-lowercase-alphanumeric-acr-name>'
$app = '<new-container-app-name>'
$voiceEndpoint = 'https://<your-foundry-resource>.services.ai.azure.com'
$liveEndpoint = 'https://<your-gpt-live-resource>.openai.azure.com'
$liveDeployment = '<your-actual-gpt-live-deployment-name>'
$version = 'recognition-uat-20261104-r2'
$credentialsFile = '.local\credentials.parameters.json'

az group create --subscription $subscription --name $group --location $location -o none
az acr create --subscription $subscription --resource-group $group --name $registry `
  --location $location --sku Basic -o none
az acr config authentication-as-arm show --subscription $subscription --registry $registry
```

ACR names must be globally unique, 5-50 alphanumeric characters; use lowercase.
The app name must be shorter than 32 characters, start with a letter, end with a
letter/digit, and use only lowercase letters, digits and nonconsecutive hyphens.

The registry must allow ARM-audience tokens for managed-identity image pulls.
If the preceding check reports `disabled`, get approval before enabling it:

```powershell
az acr config authentication-as-arm update --subscription $subscription `
  --registry $registry --status enabled
```

Do not silently change a shared registry's policy or enable its admin credentials.
For an approved existing registry in this resource group, skip `az acr create`
and verify its permission mode/policy instead.

```powershell
az acr build --subscription $subscription --registry $registry `
  --image "recognition-uat:$version" --file Dockerfile .
if ($LASTEXITCODE -ne 0) { throw 'Image build failed; stop before deployment.' }
$server = az acr show --subscription $subscription --name $registry --query loginServer -o tsv
$digest = az acr repository show --subscription $subscription --name $registry `
  --image "recognition-uat:$version" --query digest -o tsv
if ($LASTEXITCODE -ne 0 -or -not $digest) { throw 'Cannot resolve the built image digest.' }
$image = "$server/recognition-uat@$digest"
```

Confirm each command succeeded before continuing. Pinning the digest prevents a
later tag overwrite from silently changing the deployed code. The build runs in
ACR; Docker Desktop is not required.

## 3. Choose NEW login credentials

```powershell
.\.venv\Scripts\python.exe tools\make_credentials.py --username developer --out $credentialsFile
if ($LASTEXITCODE -ne 0) { throw 'Credential generation failed; stop before deployment.' }
```

At **New portal password (12-256 characters)**, enter a password you choose.
At **Confirm portal password**, enter the same value again; typing is hidden.
Save it in an approved password manager. **After deployment, the login is
`developer` plus that exact chosen password.**

The tool writes the username, password hash and a fresh session secret to
`$credentialsFile`; it does not write or print the plaintext password, create an
`access.json`, or retrieve an Azure-generated passcode. The directory is excluded
from Git and Docker build context, but still contains a **secret signing key**.
Restrict its filesystem permissions to your OS account before using it: on Windows,
review the `.local` folder's **Properties -> Security -> Advanced**; on Unix the
tool sets the generated file to mode `0600`. Never paste the parameter file into
an issue/chat or hand it to a tester as their password.

The generator refuses to overwrite an existing file. For a separate deployment or
rotation, use a new filename under `.local` and set `$credentialsFile` accordingly.
Do not reuse another portal's password or session secret.

## 4. Deploy hosting

```powershell
az deployment group create --subscription $subscription --resource-group $group `
  --name recognition-uat-hosting --template-file infra\hosting.bicep `
  --parameters "@$credentialsFile" `
  --parameters appName=$app registryName=$registry containerImage=$image location=$location `
    voiceLiveEndpoint=$voiceEndpoint gptLiveEndpoint=$liveEndpoint `
    gptLiveDeployment=$liveDeployment appVersion=$version -o none
if ($LASTEXITCODE -ne 0) { throw 'Hosting deployment failed; inspect the deployment before continuing.' }

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

To reuse an approved Container Apps environment in the same resource group, add
`--parameters existingEnvironmentName='<your-existing-environment-name>'` to the
deployment command. This creates a separate app and identity without updating the
existing environment or any existing app. Omit it to create a new environment.
Set `$location` to that environment's region. Use fresh login credentials for the
separate app, and retain the same existing-environment argument on later updates.

ACR/identity role propagation can delay the initial image pull. Inspect revision
health and retry once permissions have propagated rather than enabling registry
admin credentials or replacing a failed image with a success-looking placeholder.

The template does **not** assign AI access across arbitrary resource groups or
subscriptions. A successful app deployment is not proof of AI access. Complete
the next step before testing audio.

## 5. Authorize the new app identity

An administrator allowed to assign roles must grant `$principal` the following
data-plane access on the actual resources behind the endpoints:

```powershell
$voiceSubscription = $subscription
$liveSubscription = $subscription
$voiceResourceId = '<full-Voice-Live-Foundry-resource-id>'
$liveResourceId = '<full-GPT-Live-Foundry-resource-id>'

az role assignment create --subscription $voiceSubscription --assignee-object-id $principal `
  --assignee-principal-type ServicePrincipal --scope $voiceResourceId `
  --role 'f2dc8367-1007-4938-bd23-fe263f013447' -o none
az role assignment create --subscription $liveSubscription --assignee-object-id $principal `
  --assignee-principal-type ServicePrincipal --scope $liveResourceId `
  --role '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd' -o none
```

The roles are **Cognitive Services Speech User** and **Cognitive Services OpenAI
User**, respectively. Change the subscription variables if AI resources are in
other subscriptions. If both endpoints belong to one resource, use the same
resource ID for both roles. See the [Foundry role explanation](foundry-setup.md#4-authorize-the-runtime-identity).
Confirm each assignment succeeds and allow time for propagation.

These are roles for the **managed identity**, not the deployer's user. Browser
testers do not need personal Azure roles to use this hosted app; they need the
portal's shared login. Azure calls use the app identity's permissions.

## 6. Open the URL, sign in and test

```powershell
Write-Output "$url/recognition/uat"
Write-Output "$url/recognition"
```

Open the printed HTTPS URL. Sign in with **`developer`** and **the password you
chose in step 3**. If you changed `--username`, use that username instead.
There is no password to retrieve from Azure after deployment.

Verify all three real lanes, a processed answer and clean stop. Check that unauthenticated
page access redirects, unauthenticated API access returns 401, and no audio starts
before the tester explicitly starts. Follow the [first microphone test](../README.md#your-first-microphone-test)
for the exact UI sequence.

`$url/health` is public and deliberately does not claim model readiness.
If you see a failed lane, inspect provider configuration, model access, RBAC,
networking and browser WebRTC—not just revision health.

Do not enable `ALLOW_INSECURE_LOCALHOST` in Container Apps. Use private-network
hosting where your organization requires it; the server must be able to reach
the approved AI endpoints and the browser must have an approved media path.

Save the shared login in an approved password manager, and distribute it only
through approved private channels. After validation and appropriate secret
retention, remove only the temporary parameter file you generated; do not delete
the whole `.local` directory. Future IaC updates still need the intended login
parameters from an approved secret store.

## Resetting a forgotten hosted password

There is no self-service reset endpoint. The original password cannot be recovered
from `portal-password-hash`, and `portal-session-secret` is **not** a login password.
An authorized administrator can replace the credentials:

1. Reopen the repository and set the same subscription, group, app, registry,
   location, endpoint, deployment-name and version variables used for this app.
2. Select a new, unused parameter filename and choose a new password:

```powershell
$credentialsFile = '.local\credentials-reset.parameters.json'
.\.venv\Scripts\python.exe tools\make_credentials.py --username developer --out $credentialsFile
if ($LASTEXITCODE -ne 0) { throw 'Credential generation failed; do not redeploy.' }
$image = az containerapp show --subscription $subscription --resource-group $group `
  --name $app --query 'properties.template.containers[0].image' -o tsv
if ($LASTEXITCODE -ne 0 -or -not $image) { throw 'Cannot determine the current image.' }
```

Repeat **step 4's deployment command** using this new `$credentialsFile` and the
current `$image`; no image rebuild is needed for a password-only change. Retain
`existingEnvironmentName` if originally used. Stop if that deployment fails.

Updating an app secret does not automatically restart running revisions. After
the successful deployment, explicitly restart **every active revision**:

```powershell
$activeRevisions = @(az containerapp revision list --subscription $subscription `
  --resource-group $group --name $app --query '[?properties.active].name' -o tsv)
if ($LASTEXITCODE -ne 0 -or $activeRevisions.Count -eq 0) { throw 'No active revisions found.' }
foreach ($revision in $activeRevisions) {
  az containerapp revision restart --subscription $subscription --resource-group $group `
    --name $app --revision $revision -o none
  if ($LASTEXITCODE -ne 0) { throw 'Revision restart failed; complete rotation before sharing access.' }
}
```

This can briefly interrupt the app. The newly generated signing secret invalidates
old login cookies after revisions restart. Open the URL, sign in using the **new**
chosen password, validate access, and update your approved password-manager
record. Do not retain or redeploy an older parameter file that restores the old
credentials. Remove only temporary parameter files after following secret policy.

## Updating and operating

Rebuild with a new version/tag, capture its digest, and update the app image and
`APP_VERSION` together. The supplied template uses **Single** revision mode, so a
ready update becomes live; it does not stage a zero-traffic revision. Use a separate
test app, or an approved Multiple-revision promotion workflow, if pre-promotion
qualification is required. Changing secrets needs deliberate rotation and active
revision restart as described above.

Browser-local UAT records are not a database or backup. Export approved synthetic
records before clearing storage. Logs should contain provider error codes, not
customer audio, transcripts, passwords, bearer tokens or SDP bodies.

The Bicep files are development templates, not a claim that private networking,
production Entra login, centralized abuse controls, monitoring retention or a
real call-center telephony integration has been implemented.

## Official references

- [Container Apps CLI setup and provider registration](https://learn.microsoft.com/en-us/azure/container-apps/quickstart-code-to-cloud)
- [Resource provider registration and permissions](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/resource-providers-and-types)
- [Managed-identity image pulls and ACR ARM-audience policy](https://learn.microsoft.com/en-us/azure/container-apps/managed-identity-image-pull)
- [App secrets, revision restart and rotation](https://learn.microsoft.com/en-us/azure/container-apps/manage-secrets)
