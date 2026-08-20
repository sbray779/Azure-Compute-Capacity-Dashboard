# Azure capacity/quota request integration recommendations (as of 2026-08-11)

## Bottom line
**Prefer the Microsoft.Quota REST API for supported quotas.** It is GA, purpose-built for quota management, exposes usage/quota/request-status APIs, and is the cleanest programmatic contract for an external customer workflow. Use **Azure CLI** only as an operator/script wrapper, **Az PowerShell** only with caution because **Az.Quota is still preview**, and **Azure Support REST API** only when the quota flow returns `ContactSupport`/similar errors or the quota/service is not self-service through Microsoft.Quota. [Azure Quota REST API](https://learn.microsoft.com/en-us/rest/api/quota/) • [Azure CLI quota](https://learn.microsoft.com/en-us/cli/azure/quota?view=azure-cli-latest) • [Az.Quota module](https://learn.microsoft.com/en-us/powershell/module/az.quota/?view=azps-16.2.0) • [Azure Support REST API](https://learn.microsoft.com/en-us/rest/api/support/)

## Option hierarchy
| Option | Use when | Recommendation |
|---|---|---|
| **Microsoft.Quota REST API** | Supported quota/resource providers | **Primary** |
| **Azure CLI `az quota`** | Human-operated scripts/ops tooling | Secondary wrapper |
| **Azure PowerShell `Az.Quota`** | PowerShell-only environments | Avoid as primary; **preview module** |
| **Azure Support REST API** | Unsupported/manual quota paths, rejected/self-service-unavailable requests | Fallback/manual escalation |

## What Microsoft.Quota covers
The GA Quota Service API uses the **Microsoft.Quota** resource provider and Microsoft documents support for **Azure virtual machines (cores/vCPU), Azure Machine Learning, Networking, Azure HPC Cache, Storage, and Azure Purview**. It supports: get current quota, get usage, list quotas/usages, create or update quota, get request status, and list request history for one year. [Docs](https://learn.microsoft.com/en-us/rest/api/quota/)

## Microsoft.Quota REST API (preferred)
Use these ARM endpoints against `https://management.azure.com`:
- **Get current quota**: `GET {scope}/providers/Microsoft.Quota/quotas/{resourceName}?api-version=2025-09-01` [docs](https://learn.microsoft.com/en-us/rest/api/quota/quota/get?view=rest-quota-2025-09-01)
- **Get current usage**: `GET {scope}/providers/Microsoft.Quota/usages?api-version=2025-09-01` [docs](https://learn.microsoft.com/en-us/rest/api/quota/usages/list?view=rest-quota-2025-09-01)
- **Submit increase/update**: `PUT {scope}/providers/Microsoft.Quota/quotas/{resourceName}?api-version=2025-09-01` [docs](https://learn.microsoft.com/en-us/rest/api/quota/quota/create-or-update?view=rest-quota-2025-09-01)
- **Get a request by ID**: `GET {scope}/providers/Microsoft.Quota/quotaRequests/{id}?api-version=2025-09-01` [docs](https://learn.microsoft.com/en-us/rest/api/quota/quota-request-status/get?view=rest-quota-2025-09-01)
- **List request history** (last year): `GET {scope}/providers/Microsoft.Quota/quotaRequests?...` [docs](https://learn.microsoft.com/en-us/rest/api/quota/quota-request-status/list?view=rest-quota-2025-09-01)

**Lifecycle:** GET quota + usage first, calculate desired limit, then PUT. PUT can return **200** or **202**; on async responses, honor `Location` and `Retry-After`. Request states documented by Microsoft are **Accepted, Invalid, InProgress, Succeeded, Failed**. [Create/Update](https://learn.microsoft.com/en-us/rest/api/quota/quota/create-or-update?view=rest-quota-2025-09-01) • [Request status](https://learn.microsoft.com/en-us/rest/api/quota/quota-request-status/get?view=rest-quota-2025-09-01)

## Azure CLI / PowerShell wrappers
- **Azure CLI**: `az quota` extension is GA and auto-installs on CLI 2.54.0+. Useful commands: `az quota show|list|usage list|create|request show|request list`. [docs](https://learn.microsoft.com/en-us/cli/azure/quota?view=azure-cli-latest) / [request commands](https://learn.microsoft.com/en-us/cli/azure/quota/request?view=azure-cli-latest)
- **Azure PowerShell**: `Get-AzQuota`, `Get-AzQuotaUsage`, `New-AzQuota`, `Get-AzQuotaRequestStatus` exist, but **Az.Quota is explicitly marked Preview and not recommended for production**. [module](https://learn.microsoft.com/en-us/powershell/module/az.quota/?view=azps-16.2.0) • [New-AzQuota](https://learn.microsoft.com/en-us/powershell/module/az.quota/new-azquota?view=azps-16.2.0)

**Recommendation:** for an external customer product, call REST directly; do not shell out to CLI/PowerShell from your service unless you are building internal admin tooling.

## When Azure Support REST API is required
Use Support only when Microsoft.Quota cannot complete the request (for example documented Quota API errors such as `ContactSupport`, `QuotaNotAvailableForResource`, `ResourceNotAvailableForSubscription`, `UnableToIncreaseQuota`) or when the target limit is not self-service/adjustable. Microsoft explicitly recommends opening a **Service and subscription limits (quotas)** support request in these cases. [Quota API troubleshooting](https://learn.microsoft.com/en-us/rest/api/quota/) • [Create support ticket](https://learn.microsoft.com/en-us/rest/api/support/support-tickets/create?view=rest-support-2024-04-01)

Support flow:
1. **List services**: `GET /providers/Microsoft.Support/services?api-version=2024-04-01` [docs](https://learn.microsoft.com/en-us/rest/api/support/services/list?view=rest-support-2024-04-01)
2. **List problem classifications** for the chosen service: `GET /providers/Microsoft.Support/services/{serviceName}/problemClassifications?api-version=2024-04-01` [docs](https://learn.microsoft.com/en-us/rest/api/support/problem-classifications/list?view=rest-support-2024-04-01)
3. **Create ticket**: `PUT /subscriptions/{subscriptionId}/providers/Microsoft.Support/supportTickets/{supportTicketName}?api-version=2024-04-01` [docs](https://learn.microsoft.com/en-us/rest/api/support/support-tickets/create?view=rest-support-2024-04-01)
4. **Track ticket**: `GET .../supportTickets/{supportTicketName}`; Microsoft says ticket data is available for **18 months**. [docs](https://learn.microsoft.com/en-us/rest/api/support/support-tickets/get?view=rest-support-2024-04-01)

Important limitations from Microsoft:
- Always fetch **services** and **problem classifications** programmatically because IDs can change. [services](https://learn.microsoft.com/en-us/rest/api/support/services/list?view=rest-support-2024-04-01) • [problem classifications](https://learn.microsoft.com/en-us/rest/api/support/problem-classifications/list?view=rest-support-2024-04-01)
- **Attachments are not supported** via the Support REST API.
- **Diagnostic-consent collection is not supported** via the API.
- Portal guidance states Azure provides **unlimited support for subscription management**, including **quota adjustments**; technical support still depends on support plan. [portal doc](https://learn.microsoft.com/en-us/azure/azure-portal/supportability/how-to-create-azure-support-request)

## Authentication and RBAC
- All of these are ARM APIs; obtain a Microsoft Entra access token and send it to `management.azure.com` in the `Authorization` header. Microsoft shows token acquisition via **Azure CLI** (`az account get-access-token`) or **Azure PowerShell** (`Get-AzAccessToken`). [docs](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/manage-resources-rest)
- Before using Quota API, Microsoft requires registering **Microsoft.Quota** and assigning **Quota Request Operator** (role ID `0e5f05e5-9ab9-446b-b98d-1e2157c94125`). [docs](https://learn.microsoft.com/en-us/rest/api/quota/)
- For support tickets, Microsoft requires **Owner, Contributor, Support Request Contributor**, or a custom role with `Microsoft.Support/*` at **subscription scope**. [docs](https://learn.microsoft.com/en-us/azure/azure-portal/supportability/how-to-create-azure-support-request)
- For cross-tenant / CSP on-behalf-of scenarios, Microsoft documents `x-ms-authorization-auxiliary`; the auxiliary header can hold up to **three** tokens. [ARM multi-tenant auth](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/authenticate-multi-tenant) • [support create](https://learn.microsoft.com/en-us/rest/api/support/support-tickets/create?view=rest-support-2024-04-01)

## Recommended architecture
1. **Customer-facing UI** collects subscription, region, service/resource family, current demand, and business justification.
2. **Backend API** performs all ARM calls server-side.
3. Backend uses **Microsoft.Quota first**: GET quota + usage, validate `isQuotaApplicable`, then PUT quota.
4. Persist `scope`, `resourceName`, desired limit, actor, request timestamps, returned quota request ID / support ticket name, and final outcome.
5. If Quota API returns a support-required condition, create a **Support REST** ticket automatically or route to assisted/manual handling.
6. Poll quota request/ticket status until terminal state, then notify customer and update your system of record.

## Security, idempotency, and auditing
- Use **least privilege**: Quota Request Operator for quota automation; Support Request Contributor only where support ticket creation is needed.
- Prefer a **server-side app/service principal or managed identity-backed backend** over direct browser-to-ARM calls.
- Treat Quota PUT as an **upsert** to a natural key of `(subscription, scope, resourceName)` and keep your own request ledger; there is **no separate idempotency key** documented for quota requests.
- For Support REST, make `supportTicketName` deterministic from your external request ID so retries do not create duplicate tickets.
- Azure Monitor **Activity Log** records Azure management/control-plane operations by default, retains them for **90 days**, and can be exported via diagnostic settings for longer retention. Use it plus your application ledger for auditability. [docs](https://learn.microsoft.com/en-us/azure/azure-monitor/platform/activity-log)
- Respect documented throttling and `Retry-After`; Microsoft warns quota APIs can be throttled. [docs](https://learn.microsoft.com/en-us/rest/api/quota/)

## Practical recommendation
- **Preferred:** direct **Microsoft.Quota REST API** integration.
- **Fallback:** **Azure Support REST API** only for non-self-service/manual cases.
- **Do not prefer:** CLI/PowerShell wrappers for customer-facing integration; CLI is acceptable for ops scripts, and PowerShell is additionally weakened by **preview** status.


