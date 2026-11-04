# Foundry resources and access checklist

This is a custom FastAPI/WebSocket/WebRTC application, **not a Foundry Agent Service
deployment**. A Foundry project or hosted-agent container is not required.

## 1. Select the resource and region

Use a Microsoft Foundry resource (`Microsoft.CognitiveServices/accounts`,
`kind: AIServices`, S0). The source implementation was qualified in **East US 2**.
Check the current [Voice Live model/region table][regions] and GPT-Live access in your
own subscription before creating anything. Availability and inference processing
scope are not necessarily the same as the resource location.

First complete [source/tool setup](../README.md#get-the-source-and-tools) and
select the intended Entra tenant/subscription. Creating a resource requires
control-plane permission; assigning inference roles requires separate
role-assignment permission. Both configured AI resources must accept tokens from
the runtime identity's tenant for the authentication paths in this repository.

For a **new development resource**, the optional template can be deployed from
PowerShell after choosing your own subscription/group/name:

```powershell
$subscription = '<your-subscription-id>'
$group = '<new-or-approved-development-resource-group>'
$foundry = '<globally-unique-foundry-resource-name>'
az provider register --subscription $subscription --namespace Microsoft.CognitiveServices --wait
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

Also record each resource's **full resource ID** and **subscription ID** for
permission assignments. In the Azure portal, open the Foundry resource's
**Overview -> JSON View** and copy `id`, or query your approved resource:

```powershell
az cognitiveservices account show --subscription '<ai-resource-subscription-id>' `
  --resource-group '<ai-resource-group>' --name '<ai-resource-name>' `
  --query '{resourceId:id,endpoint:properties.endpoint}' -o json
```

If the two endpoints use one resource, its ID is used for both roles. If they use
different resources, record both IDs/subscriptions.

Use **resource roots**, not a Foundry project URL, portal URL, `/openai/v1` URL or
WebSocket URL. The application adds paths and API versions.
Do not copy the original owner's resource names, subscription IDs or secrets.

## 4. Authorize the runtime identity

Locally this is the signed-in developer's Azure CLI principal; hosted it is the
app's managed identity, **not** the person who deployed the app.

For this direct-endpoint runtime, start with **Cognitive Services Speech User** for
Voice Live and **Cognitive Services OpenAI User** for GPT-Live at the resource scope.
The Speech User role includes the `SpeechServices/voicelive/realtime/*` data action.
This is the narrower combination used for the isolated test app; it does not grant
the app resource-management or role-assignment privileges.

The general Voice Live quick start documents **Cognitive Services User** and
**Foundry User** for broader Foundry access. This sample does not create projects
or invoke Foundry Agents. Have an administrator review the current role definitions,
tenant policy and actual endpoint errors rather than granting broader roles by
default.

The following documented built-in IDs avoid confusion during role renaming:

| Role | Stable role ID |
|---|---|
| Cognitive Services Speech User | `f2dc8367-1007-4938-bd23-fe263f013447` |
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
To find your own CLI user's object ID, use
`az ad signed-in-user show --query id -o tsv` after signing in to the correct
tenant, or ask your tenant administrator for the object ID in that tenant.
An administrator can then grant that user **both** documented role IDs, on the
Voice Live and GPT-Live resource scopes respectively. The
[Azure hosting walkthrough](azure-hosting.md#5-authorize-the-new-app-identity)
provides the concrete assignment commands for the app's managed identity.
The application requests `https://ai.azure.com/.default` and never sends an Azure
token or API key to the browser. Do not give the app Owner/Contributor simply to
avoid diagnosing an inference permission error.

### Local development: Azure CLI identity

The [Windows quick start](../README.md#quick-start-on-windows) runs the complete
portal on loopback; Azure Container Apps, ACR and a managed identity are not
required. The Foundry resource/model prerequisites above still apply.

1. Sign in with `az login`. For a resource in another tenant, use
   `az login --tenant '<resource-tenant-id>'`.
2. Select your approved subscription with
   `az account set --subscription '<your-subscription-id>'` and supply the same
   subscription to `tools\run_local.py --subscription`.
3. Ensure this signed-in **developer user**, or an approved group they belong to,
   has the resource-scoped inference roles described above. A hosted app's role
   assignments do not automatically grant the developer access.
4. Run the launcher from the README and choose a new local portal password.
   The password is only a local demo gate, not a Microsoft/Entra credential.
5. Open `http://localhost:8000/recognition/uat`, explicitly start a synthetic test,
   and confirm all three lanes produce output. Use `--port` for a different free
   local port.

The runtime authentication chain is:

```text
az login -> Azure CLI's signed-in identity
         -> AzureCliCredential(subscription=<explicit subscription>)
         -> token for https://ai.azure.com/.default
         -> backend Authorization: Bearer header -> configured Azure AI resource
```

`tools\run_local.py` sets `AZURE_AUTH_MODE=cli` explicitly. It does not attempt
managed-identity authentication or fall through to a different credential when
CLI authentication fails. A subscription selects the CLI account context; it
does not grant access to the configured endpoint.

The backend obtains Azure tokens and uses them for Voice Live and GPT-Live
session/sideband requests. Those Entra tokens are not sent to browser JavaScript.
The browser sends the shared microphone stream to the local backend for Voice
Live and uses WebRTC media for native GPT-Live. No Azure resource API key, app
registration or client secret is required for this developer-account path.

The launcher binds only to `127.0.0.1`; its HTTP-cookie exception accepts only
loopback hosts and clients. Keep the browser on `localhost`, not a LAN address.
If `az login` expires or Conditional Access requires reauthentication, sign in
again in the correct tenant. Diagnose 401/403 errors against the **local user's**
permissions, token tenant, resource endpoint and role propagation rather than
copying a hosted managed identity's client ID.

## 5. Confirm functionality, not just provisioning

Start the app, log in, select a synthetic case, explicitly enable/consent/start, and
verify all three lanes reach listening. Speak a short synthetic answer, stop and
inspect heard/processed output. Then exercise negatives and silence.

`/health`, a populated deployment list, and a 200 configuration response do not
prove model access or successful browser media. A proxy/firewall can still block
WebRTC even while HTTPS and the backend WebSocket work.

### Recorded local smoke: November 4, 2026

The published `tools\run_local.py` entry point was exercised on Windows with
Python 3.12, loopback HTTP and real `AzureCliCredential` authentication against
existing qualified Foundry endpoints. No Azure hosting resources, identities or
role assignments were created or changed. The developer account already had
broader permissions including Cognitive Services User and Foundry User; this was
not a new least-privilege developer-account qualification.

Both portal pages reached listening on all three providers. Synthetic audio was
fed through one browser microphone stream, exercising the real browser worklet,
backend WebSocket, Azure inference and native GPT-Live WebRTC/sideband path:

| Synthetic input | Speech + Luna | Native Realtime | Native GPT-Live |
|---|---|---|---|
| Generic code: `D E M O` followed by sixty-three | `DEMO63` | `DEMO63` | `DEMO63` |
| Renewal through the generic telecom service app | `{"status":"valid","value":"online"}` | `{"status":"valid","value":"online"}` | `{"status":"valid","value":"online"}` |

Portal login, unauthenticated access gates, rejection of non-loopback HTTP hosts,
localhost's browser secure context and clean native-session stop were also
checked; no browser script errors were observed. Password-prompt input alone was
automated for the test. Azure credentials and model outputs were not mocked.
This is functional smoke evidence, not physical-microphone, human-accent,
telephony-accuracy or fresh-subscription provisioning evidence.

## Primary sources

Reviewed November 4, 2026:

- [Voice Live managed models and pricing tiers][voice]
- [Voice Live authentication, endpoints and session settings][howto]
- [Voice Live model/region and processing scopes][regions]
- [Voice Live API 2026-07-15 reference][reference]
- [GPT-Live prerequisites, model deployment and sideband connection][live]
- [GPT-Live strict session configuration and timed fragments][liveref]
- [DefaultAzureCredential for Python][identity]
- [AzureCliCredential and explicit subscription selection][clicredential]
- [Local development with an Entra developer account][localauth]
- [Built-in Speech/OpenAI role data actions][roles]

[voice]: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live#supported-models-and-regions
[howto]: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live-how-to#authentication
[regions]: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/regions?tabs=voice-live#regions
[reference]: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live-api-reference-2026-07-15
[live]: https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/gpt-live
[liveref]: https://learn.microsoft.com/en-us/azure/foundry/openai/gpt-live-reference
[identity]: https://learn.microsoft.com/en-us/python/api/azure-identity/azure.identity.aio.defaultazurecredential
[clicredential]: https://learn.microsoft.com/en-us/python/api/azure-identity/azure.identity.aio.azureclicredential
[localauth]: https://learn.microsoft.com/en-us/azure/developer/python/sdk/authentication/local-development-dev-accounts
[roles]: https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/ai-machine-learning
