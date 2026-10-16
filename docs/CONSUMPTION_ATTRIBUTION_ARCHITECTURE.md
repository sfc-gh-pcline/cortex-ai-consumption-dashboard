# Cortex AI Consumption Attribution for Service Account Workloads

This is an architecture proposal, not a deployment guide for an implemented external-identity
dashboard. Patterns A-D, the identity cascade, and the proposed reporting page require
implementation and validation. The AI Functions warehouse-membership subsection describes
current behavior separately. SCIM payload and budget integration examples still require
product-specific validation before use. For the shipped dashboard's behavior, use the
[metrics contract](DASHBOARD_METRICS.md).

## Overview

Organizations running Cortex Agents and Cortex Search often operate through service accounts rather than individual Snowflake users. This creates a consumption attribution gap: the Snowflake account sees credits consumed by `SVC_AGENT_PROD`, but the business needs to know which external team, application, or end-user drove that consumption.

This document describes the architecture for correlating both **serverless credits** (Cortex Agent inference, Cortex Search queries) and **warehouse compute credits** (SQL tool invocations, batch search) back to external business identities.

---

## Problem Statement

| Scenario | What Snowflake Records | What the Business Needs |
|----------|----------------------|------------------------|
| Single service account runs agent on behalf of many users | `USER_NAME = SVC_AGENT_PROD` | Which end-user or team triggered each agent call |
| External OAuth flow maps multiple identities to one Snowflake user | `USER_NAME = OAUTH_SVC` | The original OAuth subject (e.g., `john@acme.com`) |
| Key-pair service accounts per application | `USER_NAME = APP_SEARCH_SVC` | The business unit or cost center that owns the app |
| Cortex Agent REST API caller | `USER_NAME = REST_CALLER` | The external system or user that initiated the request |

---

## Available Snowflake Signals

### Serverless Credit Attribution (Cortex Agents)

**`SNOWFLAKE.ACCOUNT_USAGE.CORTEX_AGENT_USAGE_HISTORY`**

Each row represents one agent invocation with:

- `USER_NAME` / `USER_ID`: the authenticated Snowflake user
- `USER_TAGS` (ARRAY): tags applied to the user, including custom tags like `cost_center` or `external_user_id`
- `REQUEST_ID`: unique correlation key for the entire agent call
- `AGENT_NAME`: which agent was invoked
- `TOKEN_CREDITS`: token credits for the request, not warehouse or AI-function-inclusive total cost
- `CREDITS_GRANULAR`: per-model, per-service-type breakdown
- `METADATA.role_name`: the role used
- `METADATA.interaction_interface`: how the agent was accessed (`sql_function`, `external`, `microsoft_teams`, etc.)

### Warehouse Credit Attribution (Agent SQL Tools, Batch Search)

**`SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY`**

Each row represents one query with attributed warehouse compute cost:

- `QUERY_ID`: joins to other views
- `QUERY_TAG`: session-level tag (can carry structured JSON with external user identity)
- `USER_NAME`: the Snowflake user that ran the query
- `CREDITS_ATTRIBUTED_COMPUTE`: weighted compute cost (excludes idle time)
- `PARENT_QUERY_ID` / `ROOT_QUERY_ID`: links child queries to the stored procedure or agent that spawned them

**`SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY`** (supplementary metadata)

- `AGENT_TYPE`: classifies how the query was triggered:
  - `CORTEX_AGENT`: persistent named agent
  - `CORTEX_LITE_AGENT`: stateless REST/CoCo agent
  - `EXTERNAL_AGENT`: external agent using `SERVICE_AGENT` user type or OAuth with `IS_AGENTIC = TRUE`
- `QUERY_TAG`: the tag set on the session
- `SESSION_ID`: links to SESSIONS view for auth method

### Cortex Search Attribution

**`SNOWFLAKE.ACCOUNT_USAGE.CORTEX_SEARCH_BATCH_QUERY_USAGE_HISTORY`**

- `QUERY_ID`: joinable to `QUERY_ATTRIBUTION_HISTORY` for warehouse cost attribution
- `CREDITS_USED`: serverless search credits for batch queries

**`SNOWFLAKE.ACCOUNT_USAGE.CORTEX_SEARCH_DAILY_USAGE_HISTORY`**

- Credits per service per day (serving, embedding, batch)
- No user-level column: serving costs are infrastructure-level (per GB-month of indexed data)

### AI Functions warehouse membership

Current implementation: the dashboard identifies AI Functions queries through distinct `QUERY_ID` values in
`SNOWFLAKE.ACCOUNT_USAGE.CORTEX_AI_FUNCTIONS_USAGE_HISTORY`. The usage view supplies
membership, not warehouse cost. Warehouse attribution and optional adaptive metering
remain the cost sources. Multiple function, model, or hourly records must not multiply
a query's warehouse credits.

Tagged Agents, CoWork, and CoCo queries take precedence over Search membership, which
takes precedence over AI Functions membership. The supported AI source starts on
January 5, 2026. Earlier attribution is incomplete, and recent data remains subject to
latency and retention. Updating an existing installed task requires a separately planned
migration and rollback; updating the app alone does not replace its SQL.

### Identity Resolution Signals

| Signal | Source | Best For |
|--------|--------|----------|
| `USER_TAGS` | `CORTEX_AGENT_USAGE_HISTORY` | Tag-based cost center attribution (works with budgets) |
| `QUERY_TAG` (JSON) | `QUERY_ATTRIBUTION_HISTORY`, `QUERY_HISTORY` | Per-request external user identity |
| `TAG_REFERENCES` (domain=USER) | `ACCOUNT_USAGE.TAG_REFERENCES` | User-to-department mapping |
| `AUTHENTICATION_METHOD` | `SESSIONS` | Distinguishing OAuth vs. key-pair vs. password |
| `agent_type` | `QUERY_HISTORY` | Identifying agent-driven warehouse work |
| Mapping table | Customer-managed | Fallback when tags/query-tags are absent |

---

## Architecture: Multi-Signal Correlation

The attribution model resolves external identity through a priority-based cascade:

```text
Priority 1: QUERY_TAG (per-request, highest fidelity)
    └─ JSON field: {"external_user": "...", "team": "...", "request_id": "..."}

Priority 2: USER_TAGS (per-user, set at provisioning)
    └─ Tag: external_user_id = "team_alpha"

Priority 3: TAG_REFERENCES (per-user, managed centrally)
    └─ Tag: cost_center = "engineering"

Priority 4: Mapping table (fallback, customer-managed)
    └─ SERVICE_ACCOUNT → EXTERNAL_IDENTITY lookup
```

### Correlation Flow

```text
                    ┌─────────────────────────────┐
                    │   EXTERNAL USER / TEAM       │
                    │   (resolved via cascade)     │
                    └──────────────┬──────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                    │
              ▼                    ▼                    ▼
 ┌────────────────────┐ ┌──────────────────┐ ┌─────────────────────┐
 │  CORTEX AGENT      │ │  WAREHOUSE       │ │  CORTEX SEARCH      │
 │  SERVERLESS        │ │  COMPUTE         │ │                     │
 │                    │ │                  │ │                     │
 │  View:             │ │  View:           │ │  Batch: joinable    │
 │  CORTEX_AGENT_     │ │  QUERY_          │ │  via QUERY_ID       │
 │  USAGE_HISTORY     │ │  ATTRIBUTION_    │ │                     │
 │                    │ │  HISTORY         │ │  Serving: allocated │
 │  Key: USER_TAGS,   │ │                  │ │  proportionally     │
 │  REQUEST_ID        │ │  Key: QUERY_TAG, │ │                     │
 │                    │ │  agent_type      │ │                     │
 └────────────────────┘ └──────────────────┘ └─────────────────────┘
```

### Join Strategy

| Credit Type | Join Path | Granularity |
|-------------|-----------|-------------|
| Agent serverless | `CORTEX_AGENT_USAGE_HISTORY.USER_NAME` → identity resolution | Per-request |
| Warehouse (agent-triggered) | `QUERY_ATTRIBUTION_HISTORY` JOIN `QUERY_HISTORY` ON `QUERY_ID` WHERE `agent_type IS NOT NULL` | Per-query |
| Warehouse (agent child queries) | `QUERY_ATTRIBUTION_HISTORY.ROOT_QUERY_ID` → parent agent query | Per-query |
| Search batch | `CORTEX_SEARCH_BATCH_QUERY_USAGE_HISTORY.QUERY_ID` → `QUERY_ATTRIBUTION_HISTORY` | Per-query |
| Search serving | Proportional allocation based on query volume per user | Daily aggregate |

---

## Implementation Patterns by Scenario

### Pattern A: Structured Query Tag (Shared Service Account)

When a single service account serves multiple external users, the application sets a JSON query tag on each session:

```sql
ALTER SESSION SET QUERY_TAG = '{"external_user":"john@acme.com","team":"finance","request_id":"abc-123"}';
```

Attribution queries parse this tag to resolve the external identity. This pattern provides per-request granularity but requires application-code changes.

**Applicability:** Applications, middleware, or agent frameworks that call Snowflake on behalf of multiple end-users through a single connection.

### Pattern B: User Tags via SCIM (Dedicated Service Accounts)

When each service account maps to a single external identity or team, tags are applied at provisioning time via SCIM:

```json
{
  "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User": {
    "snowflakeTags": "[attribution.tags.cost_center:finance]"
  }
}
```

These tags appear natively in `CORTEX_AGENT_USAGE_HISTORY.USER_TAGS` and work with Snowflake's budget system.

**Applicability:** Organizations with one service account per team/application where identity is stable.

### Pattern C: External OAuth with IS_AGENTIC

For OAuth flows where the token carries the external user's identity:

1. The OAuth security integration maps token claims to Snowflake users via `EXTERNAL_OAUTH_TOKEN_USER_MAPPING_CLAIM`
2. Queries from these sessions show `agent_type = 'EXTERNAL_AGENT'` in `QUERY_HISTORY`
3. The `AUTHN_EVENT_ID` in `QUERY_HISTORY` links to `LOGIN_HISTORY` for full auth context

**Applicability:** External AI agents authenticating via OAuth with agentic identity.

### Pattern D: Key-Pair Service Accounts with Mapping Table

For organizations with multiple key-pair service accounts where identity is managed outside Snowflake:

1. A mapping table maintains `SERVICE_ACCOUNT → EXTERNAL_TEAM` with validity periods (SCD-2)
2. Attribution queries LEFT JOIN to the mapping table as a fallback when QUERY_TAG and USER_TAGS are absent

**Applicability:** Legacy integrations where application-code changes are not feasible.

---

## What Cannot Be Attributed

| Cost Category | Reason | Mitigation |
|--------------|--------|------------|
| Cortex Search serving credits | Infrastructure cost (per GB-month of indexed data), not per-user | Allocate proportionally based on batch query volume per user |
| Warehouse idle time | Not attributed to any query | Distribute proportionally across active users (same approach as Snowflake docs) |
| Queries ≤100ms | Below attribution threshold | Accept as de minimis; these are typically metadata queries |
| Cortex Agent calls via CoWork | Attributed to CoWork, not the agent | Use `SNOWFLAKE_COWORK_USAGE_HISTORY` separately |

---

## Snowflake Budget Integration

The `USER_TAGS` signal integrates directly with Snowflake's shared resource budget system:

1. **Shared resource budgets** can scope Cortex Agent spend per tagged user group
2. **Warehouse shared resource budgets** can scope compute spend per tagged user group
3. Budget enforcement operates on a periodic cycle (2h with low-latency, 8h standard)

This means organizations can use the same tags for both **attribution reporting** (this dashboard) and **cost control** (budgets with threshold actions).

---

## Dashboard Integration

The attribution page in the consumption dashboard will provide:

1. **Summary KPIs**: Total attributed credits (serverless + warehouse) by external user/team
2. **Trend charts**: Daily/weekly credit consumption by external identity
3. **Breakdown tables**: Per-agent, per-search-service attribution
4. **Coverage metrics**: Percentage of credits successfully attributed vs. unattributed
5. **Flexible filters**: By attribution pattern, external user, team, agent, date range

Granularity options:

- Per-request (via REQUEST_ID)
- Per-user (via resolved external identity)
- Per-team (via cost_center tag or team field)
- Daily rollup

---

## Proposed implementation prerequisites

1. **Grant access** to the `SNOWFLAKE.ACCOUNT_USAGE` views: the `USAGE_VIEWER` database role covers the usage and attribution histories, `GOVERNANCE_VIEWER` covers `TAG_REFERENCES` and `QUERY_HISTORY`, and `SECURITY_VIEWER` covers `USERS`, `SESSIONS`, and `LOGIN_HISTORY` (or use `ACCOUNTADMIN`)
2. **Apply tags** to service account users (via SCIM, ALTER USER, or the Tag Management UI)
3. **Set query tags** in application code (if using Pattern A)
4. **Create mapping table** (if using Pattern D as fallback)
5. **Verify data availability**: standard warehouse attribution can lag by up to eight hours. Agent main usage can lag by up to one hour; SQL metadata can lag by up to eight hours. See [source timing](DASHBOARD_METRICS.md#source-timing).

---

## Next Steps

- [ ] Validate data availability in your account (which views are populated)
- [ ] Determine which attribution pattern(s) match your service account topology
- [ ] Apply tags to service accounts (if not already done)
- [ ] Configure structured QUERY_TAG in application code (if applicable)
- [ ] Implement and validate the proposed attribution page before planning its deployment
- [ ] Optionally configure shared resource budgets for cost control

---

*This document describes the architectural direction. Detailed implementation SQL, dashboard code, and deployment instructions will be added after validation testing.*
