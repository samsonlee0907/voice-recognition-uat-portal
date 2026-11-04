# Foundry resources and access checklist

This is a custom FastAPI/WebSocket/WebRTC application, **not a Foundry Agent Service
deployment**. A Foundry project or hosted-agent container is not required.

## 1. Select the resource and region

Use a Microsoft Foundry resource (`Microsoft.CognitiveServices/accounts`,
`kind: AIServices`, S0). The source implementation was qualified in **East US 2**.
Check the current [Voice Live model/region table][regions] and GPT-Live access in your
own subscription before creating anything. Availability and inference processing
scope are not necessarily the same as the resource location.

For a **new development resource**, the optional template can be deployed from
PowerShell after choosing your own subscription/group/name:

```powershell
$subscription = '<your-subscription-id>'
$group = '<new-or-approved-development-resource-group>'
$foundry = '<globally-unique-foundry-resource-name>'
az group create --subscription $subscription --name $group --location eastus2
az deployment group create --subscription $subscription --resource-group $group `
  --template-file infra\foundry.bicep --parameters name=$foundry location=eastus2
```

This template creates a new, keyless, publicly reachable dev resource. It does not
alter an existing resource, grant your identity roles, deploy GPT-Live, or request
model access. For restricted environments use an approved private-network design
instead; do not loosen an existing account's network rules to make this sample work.

An existing Foundry resource can be used without running this template.

## 2. Understand which models need a deployment

| Route | Selection | Provisioning action |
|---|---|---|
| Speech + Luna | Voice Live `model=gpt-6-luna`, integrated `azure-speech`, `zh-TW` | Confirm these managed capabilities are available. No separate Luna model deployment. |
| Native Realtime | Voice Live `model=gpt-realtime-2.1`, `input_audio_transcription:null` | Confirm the managed native model is available. No separate Realtime model deployment. |
| Native Live | GPT-Live `/openai/v1/live/sessions`, `session.model=<deployment-name>` | **Deploy `gpt-live-1`** and configure its actual deployment name. |

Microsoft documents Voice Live's native model catalog as fully managed; it does not
require separate model deployments or provisioned throughput for this path.
That is different from a bring-your-own-model/agent architecture.

For GPT-Live, open your resource in the Foundry portal, locate `gpt-live-1` in the
model catalog, and create a deployment with an available version, supported SKU and
quota. Record its **deployment name**, which can differ from the model name.
The source used `gpt-live-1` / GlobalStandard; this does not guarantee access or
capacity in another subscription. Do not substitute `gpt-live-transcribe` or another
Realtime model if access is unavailable: report the missing lane explicitly.

## 3. Record endpoints

- `VOICE_LIVE_ENDPOINT`: `https://<name>.services.ai.azure.com`, or the existing
  resource's supported `.cognitiveservices.azure.com` root.
- `GPT_LIVE_ENDPOINT`: `https://<name>.openai.azure.com`.
- `GPT_LIVE_DEPLOYMENT`: the actual GPT-Live deployment name.

Use **resource roots**, not a Foundry project URL, portal URL, `/openai/v1` URL or
WebSocket URL. The application adds paths and API versions.
Do not copy the original owner's resource names, subscription IDs or secrets.

## 4. Authorize the runtime identity

Locally this is the signed-in developer's Azure CLI principal; hosted it is the
app's managed identity, **not** the person who deployed the app.

Voice Live's current keyless guidance calls for **Cognitive Services User** and
**Foundry User** at the resource scope. Foundry User was previously named Azure AI
User; the role ID remains unchanged. GPT-Live also needs OpenAI inference
permissions; **Cognitive Services OpenAI User** is the dedicated built-in role.
An administrator should select the least-privilege combination appropriate to
their tenant's effective permissions.

The following documented built-in IDs avoid confusion during role renaming:

| Role | Stable role ID |
|---|---|
| Cognitive Services User | `a97b65f3-24c7-4388-baec-2e87135dc908` |
| Foundry User (formerly Azure AI User) | `53ca6127-db72-4b80-b1b0-d745d6d5456d` |
| Cognitive Services OpenAI User | `5e0bd9bd-7b93-4f28-af87-19fc36ad61bd` |

Assign at **each relevant resource scope**, including both if the endpoints belong
to different resources. Allow time for role propagation. Contributor/control-plane
access alone is not proof of data-plane inference access.

For example, an administrator with role-assignment permission can run:

```powershell
$principalId = '<developer-object-id-or-managed-identity-principal-id>'
$scope = '<full-Foundry-resource-id>'
az role assignment create --subscription $subscription --assignee-object-id $principalId `
  --assignee-principal-type ServicePrincipal --role '<appropriate-role-id>' --scope $scope
```

Use `--assignee-principal-type User` instead for a developer user.
The application requests `https://ai.azure.com/.default` and never sends an Azure
token or API key to the browser. Do not give the app Owner/Contributor simply to
avoid diagnosing an inference permission error.

## 5. Confirm functionality, not just provisioning

Start the app, log in, select a synthetic case, explicitly enable/consent/start, and
verify all three lanes reach listening. Speak a short synthetic answer, stop and
inspect heard/processed output. Then exercise negatives and silence.

`/health`, a populated deployment list, and a 200 configuration response do not
prove model access or successful browser media. A proxy/firewall can still block
WebRTC even while HTTPS and the backend WebSocket work.

## Primary sources

Reviewed November 4, 2026:

- [Voice Live managed models and pricing tiers][voice]
- [Voice Live authentication, endpoints and session settings][howto]
- [Voice Live model/region and processing scopes][regions]
- [Voice Live API 2026-07-15 reference][reference]
- [GPT-Live prerequisites, model deployment and sideband connection][live]
- [GPT-Live strict session configuration and timed fragments][liveref]
- [DefaultAzureCredential for Python][identity]

[voice]: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live#supported-models-and-regions
[howto]: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live-how-to#authentication
[regions]: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/regions?tabs=voice-live#regions
[reference]: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live-api-reference-2026-07-15
[live]: https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/gpt-live
[liveref]: https://learn.microsoft.com/en-us/azure/foundry/openai/gpt-live-reference
[identity]: https://learn.microsoft.com/en-us/python/api/azure-identity/azure.identity.aio.defaultazurecredential
