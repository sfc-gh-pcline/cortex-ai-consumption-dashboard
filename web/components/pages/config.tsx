"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { useState } from "react";

import { DataTable } from "@/components/kit/data-table";
import { Metric } from "@/components/kit/empty-state";
import { Expander, PageHeader, Row as ControlRow, Section } from "@/components/kit/layout";
import { Caption, Notice } from "@/components/kit/notice";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  reportAllOk,
  reportFailures,
  validateSetupDraft,
  WAREHOUSE_SIZES,
  type LifecycleAction,
  type SetupDraft,
} from "@/lib/admin/actions";
import {
  adoptObjects,
  fetchAdminState,
  provision,
  runAction,
  saveEnabled,
  saveSetup,
  type AdminState,
} from "@/lib/client/admin-api";
import { fetchSettings, type SettingsResponse } from "@/lib/client/api";
import { BUILD_INFO, fingerprintCaption } from "@/lib/build-info";
import { useDashboard } from "@/lib/client/dashboard";
import { settingsKey } from "@/lib/client/queries";
import {
  DEPENDENCY_KINDS,
  DT_SURFACE_KEYS,
  SURFACE_MODEL_RATES,
  SURFACE_REST_API,
} from "@/lib/plans";
import type { Settings } from "@/lib/settings";

const adminStateKey = ["admin-state"] as const;

// Semantic status colors, mirroring ui.resource_status_color.
function statusClass(status: string): string {
  if (["Ready", "Active", "Configured", "Created"].includes(status))
    return "text-success font-semibold";
  if (["Suspended", "Stale snapshot", "Awaiting first refresh"].includes(status))
    return "text-warning font-semibold";
  if (["Refresh failed", "Not created", "Task missing"].includes(status))
    return "text-destructive font-semibold";
  return "text-muted-foreground font-semibold";
}

const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err));

// Port of app_pages/config.py: status, the acceleration toggle, provisioning,
// task lifecycle actions, the Setup form and teardown, all behind the admin gate.
export function ConfigPage() {
  const { preflight, settings: response } = useDashboard();

  const header = (
    <PageHeader
      eyebrow="Performance"
      title="Acceleration Settings"
      subtitle="Speed up this dashboard by materializing usage data into aggregate tables."
    />
  );
  const fingerprint = (
    <Expander title="Running build">
      <Caption>
        {fingerprintCaption()}. Each App Runtime deploy is an immutable package running in a fresh
        container, so there is no stale-module state to detect. Viewer:{" "}
        {preflight?.viewer ?? "unknown"}.
      </Caption>
      <DataTable
        rows={[
          ...BUILD_INFO.components.map((c) => ({ Component: c.label, Version: c.version })),
          ...(BUILD_INFO.node ? [{ Component: "Node.js (build)", Version: BUILD_INFO.node }] : []),
        ]}
        columns={[
          { key: "Component", label: "Component" },
          { key: "Version", label: "Version" },
        ]}
      />
    </Expander>
  );
  if (!preflight?.isAdmin) {
    return (
      <div className="flex flex-col gap-6">
        {header}
        {fingerprint}
        <Notice>
          Acceleration settings require an authorized dashboard administrator.{" "}
          {preflight?.viewer
            ? `You are signed in as ${preflight.viewer}. `
            : "Your username could not be read. "}
          The app owner grants access by listing usernames in DASHBOARD_ADMIN_USERS; the Snowflake
          role does not apply because the app runs with the owner&apos;s rights.
        </Notice>
      </div>
    );
  }
  if (!response) {
    return (
      <div className="flex flex-col gap-6">
        {header}
        {fingerprint}
        <Caption>Loading settings...</Caption>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-6">
      {header}
      {fingerprint}
      <AdminBody
        response={response}
        account={preflight.preflight.account}
        role={preflight.preflight.role}
      />
    </div>
  );
}

function AdminBody({
  response,
  account,
  role,
}: {
  response: SettingsResponse;
  account: string;
  role: string;
}) {
  const client = useQueryClient();
  const { settings, statuses, readiness, freshness, healthy, sourceModes, location } = response;
  const admin = useQuery({ queryKey: adminStateKey, queryFn: fetchAdminState });
  const [editing, setEditing] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedNotice, setSavedNotice] = useState(false);

  // After any mutation: fresh settings and health past every cache, fresh admin
  // state, and every page re-reads its data (q.clear_cache + rerun).
  const refreshAll = async () => {
    await client.fetchQuery({
      queryKey: settingsKey,
      queryFn: () => fetchSettings(true),
      staleTime: 0,
    });
    await client.invalidateQueries({ queryKey: adminStateKey });
    await client.invalidateQueries({ queryKey: ["dependency"] });
  };

  const toggle = useMutation({
    mutationFn: saveEnabled,
    onMutate: () => setSaveError(null),
    onSuccess: refreshAll,
    onError: (err) => setSaveError(`Acceleration setting was not saved: ${errorText(err)}`),
  });
  const provisionMutation = useMutation({ mutationFn: provision, onSettled: refreshAll });
  const actionMutation = useMutation({
    mutationFn: (input: { action: LifecycleAction; targets: readonly string[]; retry: boolean }) =>
      runAction(input.action, input.targets, input.retry),
    onSettled: refreshAll,
  });
  const adoptMutation = useMutation({
    mutationFn: adoptObjects,
    onMutate: () => setSaveError(null),
    onSuccess: refreshAll,
    onError: (err) => setSaveError(`Existing objects were not adopted: ${errorText(err)}`),
  });
  const setupMutation = useMutation({
    mutationFn: saveSetup,
    onMutate: () => setSaveError(null),
    onSuccess: async () => {
      setEditing(false);
      setSavedNotice(true);
      await refreshAll();
    },
    onError: (err) => setSaveError(`Configuration was not saved: ${errorText(err)}`),
  });
  const busy =
    adoptMutation.isPending ||
    toggle.isPending ||
    provisionMutation.isPending ||
    actionMutation.isPending ||
    setupMutation.isPending;

  const ready = Object.values(readiness).filter((r) => r === "Ready").length;
  const toggleDisabled = editing || busy || (!healthy && !settings.enabled);
  const state: AdminState | undefined = admin.data;
  const provisionReport = state?.provisionReport ?? null;
  const lifecycleReport = state?.lifecycleReport ?? null;
  const eligible = state?.eligible ?? { suspend: [], resume: [], refresh: [] };
  const lifecycleFailures = Object.entries(lifecycleReport?.results ?? {})
    .filter(([, v]) => v.startsWith("Failed:"))
    .map(([k]) => k);

  const rows = [
    {
      Surface: `Task WH (${settings.warehouseName || "Not configured"})`,
      "Object type": "WAREHOUSE",
      Status: settings.warehouseName ? "Configured" : "Not created",
      "Row count": "",
      "Latest successful snapshot": "",
      Diagnostic: "",
    },
    ...DT_SURFACE_KEYS.map((key) => {
      const status = statuses[key];
      return {
        Surface: key,
        "Object type": key === SURFACE_MODEL_RATES ? "GLOBAL AGGREGATE" : "AGGREGATE TABLE",
        Status: readiness[key] ?? "Unknown",
        "Latest attempt": status?.latestState ?? "",
        "Attempt time": status?.latestAttempt ?? "",
        "Row count": status?.tableExists ? status.rowCount.toLocaleString("en-US") : "",
        "Latest successful snapshot": status?.dataTimestamp
          ? status.dataTimestamp.slice(0, 19)
          : "",
        Diagnostic: status
          ? [...status.probeErrors, status.latestError].filter(Boolean).join("; ")
          : "Health probe unavailable",
      };
    }),
    {
      Surface: SURFACE_REST_API,
      "Object type": "N/A",
      Status: "ACCOUNT_USAGE only",
      "Row count": "",
      "Latest successful snapshot": "",
      Diagnostic: "",
    },
  ];

  return (
    <>
      <Caption>
        Account: {account || "Unverified"}; role: {role || "Unverified"}; target:{" "}
        {settings.database}.{settings.schema} (config table in {location.database}.{location.schema}
        )
      </Caption>
      <label className="flex items-center gap-2 text-sm">
        <Switch
          checked={editing}
          disabled={busy}
          onCheckedChange={(checked) => {
            setEditing(checked);
            setSaveError(null);
          }}
          aria-label="Edit setup"
        />
        Edit setup
        <Caption>
          Lifecycle actions are disabled while editing. Leave edit mode to discard unapplied form
          changes.
        </Caption>
      </label>
      {state && state.untracked.length > 0 && (
        <>
          <Notice tone="warning">
            Found {state.untracked.length} object(s) matching this app&apos;s naming convention that
            aren&apos;t tracked for teardown.
          </Notice>
          <Expander title="Show untracked objects">
            {state.untracked.map((f) => (
              <code key={f} className="block text-xs">
                {f}
              </code>
            ))}
          </Expander>
        </>
      )}
      <ControlRow>
        <Button
          variant="outline"
          size="sm"
          disabled={refreshing}
          onClick={async () => {
            setRefreshing(true);
            try {
              await refreshAll();
            } finally {
              setRefreshing(false);
            }
          }}
        >
          <RefreshCw
            data-icon="inline-start"
            aria-hidden
            className={refreshing ? "animate-spin" : undefined}
          />{" "}
          Refresh status
        </Button>
        <Caption>Operational status is cached for up to five minutes.</Caption>
      </ControlRow>
      <ControlRow>
        <Metric
          label="Acceleration"
          value={settings.enabled ? "Active" : "Direct source"}
          delta={settings.enabled ? "Aggregate tables" : "ACCOUNT_USAGE"}
        />
        <Metric label="Ready surfaces" value={`${ready} / ${DT_SURFACE_KEYS.length}`} />
        <Metric label="Refresh schedule" value="01:00 UTC" delta="Nightly" />
      </ControlRow>
      <label className="flex items-center gap-2 text-sm">
        <Switch
          checked={settings.enabled}
          disabled={toggleDisabled}
          onCheckedChange={(checked) => toggle.mutate(checked)}
          aria-label="Enable acceleration"
        />
        Enable acceleration
      </label>
      <Caption>
        When enabled, queries read from aggregate tables instead of ACCOUNT_USAGE. Requires at least
        one aggregate table with a completed task run.
      </Caption>
      {saveError && <Notice tone="error">{saveError}</Notice>}
      {settings.enabled ? (
        <Notice tone="success">
          Acceleration is enabled. Each source uses an eligible aggregate or direct fallback.
          {freshness ? ` Latest successful snapshot: ${freshness.slice(0, 19)}.` : ""}
        </Notice>
      ) : toggleDisabled ? (
        <Notice>
          Provision the acceleration layer and wait for one successful task run to enable it.
        </Notice>
      ) : (
        <Notice>Reading from ACCOUNT_USAGE directly.</Notice>
      )}

      <Section title="Resource status">
        <DataTable
          rows={rows}
          maxHeight={600}
          columns={[
            { key: "Surface", label: "Surface" },
            { key: "Object type", label: "Object type" },
            {
              key: "Status",
              label: "Status",
              render: (v) => <span className={statusClass(String(v))}>{String(v)}</span>,
            },
            { key: "Latest attempt", label: "Latest attempt" },
            { key: "Attempt time", label: "Attempt time" },
            { key: "Row count", label: "Row count", align: "right" },
            { key: "Latest successful snapshot", label: "Latest successful snapshot" },
            { key: "Diagnostic", label: "Diagnostic" },
          ]}
        />
        <Caption>
          Stale snapshot means more than 26 hours since success: a dashboard warning threshold, not
          a Snowflake SLA. Suspended tables can exist while routing uses direct reads.
        </Caption>
      </Section>

      <Section title="Source routing">
        <DataTable
          rows={Object.keys(DEPENDENCY_KINDS).map((dep) => ({
            Dependency: dep,
            Kind: DEPENDENCY_KINDS[dep],
            Mode: sourceModes[dep]?.[0] ?? "",
            Reason: sourceModes[dep]?.[1] ?? "",
          }))}
          columns={[
            { key: "Dependency", label: "Dependency" },
            { key: "Kind", label: "Kind" },
            { key: "Mode", label: "Mode" },
            { key: "Reason", label: "Reason" },
          ]}
        />
      </Section>

      <Expander title="Load diagnostics">
        <Caption>
          Source query attempts in this application-service process: {response.sourceQueryCount}.
          Cache hits do not count. The count resets when the service restarts.
        </Caption>
      </Expander>

      <Section title="Operations">
        <Caption>
          Create the acceleration infrastructure: a task warehouse, aggregate tables, and scheduled
          tasks for each surface.
        </Caption>
        {settings.ownedObjects.length === 0 &&
          Object.values(statuses).some((s) => s.tableExists || s.taskExists) && (
            <>
              <Notice tone="warning">
                The configured objects exist but none are registered to this app (they were created
                outside it, for example by sql/config.sql), so lifecycle actions and teardown have
                nothing to act on. Adopting registers the exact configured names that exist; nothing
                is created or changed in Snowflake.
              </Notice>
              <div>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={editing || busy}
                  onClick={() => adoptMutation.mutate()}
                >
                  Adopt existing objects
                </Button>
              </div>
            </>
          )}
        {provisionReport && (
          <>
            {provisionReport.error && <Notice tone="error">{provisionReport.error}</Notice>}
            {Object.keys(reportFailures(provisionReport)).length > 0 ? (
              <Notice tone="warning">
                Last provision run: {Object.keys(reportFailures(provisionReport)).length} surface(s)
                failed. Failed: {Object.keys(reportFailures(provisionReport)).join(", ")}
              </Notice>
            ) : reportAllOk(provisionReport) && Object.keys(provisionReport.results).length > 0 ? (
              <Notice tone="success">
                Last provision run: {Object.keys(provisionReport.results).length} surface(s)
                provisioned; initial refreshes queued, awaiting completion.
              </Notice>
            ) : null}
            <ul className="text-sm">
              {Object.entries(provisionReport.results).map(([surface, result]) => (
                <li key={surface}>
                  {result === "ok" ? "☑" : "☐"}{" "}
                  {result === "ok" ? surface : `${surface}: ${result}`}
                </li>
              ))}
            </ul>
            {Object.keys(provisionReport.steps).length > 0 && (
              <DataTable
                rows={Object.entries(provisionReport.steps).map(([step, result]) => ({
                  Step: step,
                  Result: result,
                }))}
                columns={[
                  { key: "Step", label: "Step" },
                  { key: "Result", label: "Result" },
                ]}
              />
            )}
          </>
        )}
        <ControlRow>
          <Button
            size="sm"
            disabled={
              editing || busy || !(settings.database && settings.schema && settings.warehouseName)
            }
            title="Creates task WH, aggregate tables, and scheduled INSERT OVERWRITE tasks."
            onClick={() => provisionMutation.mutate(false)}
          >
            Provision aggregate tables
          </Button>
          {provisionReport &&
            (provisionReport.error || Object.keys(reportFailures(provisionReport)).length > 0) && (
              <Button
                size="sm"
                variant="outline"
                disabled={editing || busy}
                onClick={() => provisionMutation.mutate(true)}
              >
                Retry failed provisioning
              </Button>
            )}
          {(
            [
              ["suspend", "Suspend refreshes"],
              ["resume", "Resume refreshes"],
              ["refresh", "Refresh aggregate tables"],
            ] as const
          ).map(([action, label]) => (
            <Button
              key={action}
              size="sm"
              variant="outline"
              disabled={editing || busy || eligible[action].length === 0}
              title="Applies only to verified registered tasks; refresh queues work and does not certify ingestion freshness."
              onClick={() =>
                actionMutation.mutate({ action, targets: eligible[action], retry: false })
              }
            >
              {label}
            </Button>
          ))}
        </ControlRow>
        {provisionMutation.isPending && <Caption>Provisioning infrastructure...</Caption>}
        <Caption>
          Aggregate tables refresh nightly at 01:00 UTC, retain 365 days of history, and serve
          through the prior UTC day. The task warehouse runs only during refreshes.
        </Caption>
        <Caption>
          Target: {settings.database}.{settings.schema}; only exact registered objects are eligible.
        </Caption>
        {lifecycleReport && (
          <>
            {lifecycleReport.error && <Notice tone="error">{lifecycleReport.error}</Notice>}
            {Object.keys(lifecycleReport.results).length > 0 && (
              <DataTable
                rows={Object.entries(lifecycleReport.results).map(([target, result]) => ({
                  Target: target,
                  Result: result,
                }))}
                columns={[
                  { key: "Target", label: "Target" },
                  { key: "Result", label: "Result" },
                ]}
              />
            )}
            {lifecycleFailures.length > 0 ? (
              <>
                <Notice tone="warning">
                  {lifecycleFailures.length} target(s) failed. Successful targets will not be
                  retried.
                </Notice>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={editing || busy || lifecycleReport.persistenceFailed}
                  title="Resolve configuration persistence and reconcile ownership before retrying mutations."
                  onClick={() =>
                    actionMutation.mutate({
                      action: lifecycleReport.action,
                      targets: lifecycleFailures,
                      retry: true,
                    })
                  }
                >
                  Retry failed targets
                </Button>
              </>
            ) : lifecycleReport.action === "refresh" &&
              Object.keys(lifecycleReport.results).length > 0 ? (
              <Notice>
                Refreshes queued, not completed. Resource status shows the latest observed attempt.
              </Notice>
            ) : null}
          </>
        )}
      </Section>

      <Expander title="Setup">
        <SetupForm
          key={editing ? "editing" : "saved"}
          settings={settings}
          editing={editing}
          busy={busy}
          onSave={(draft) => setupMutation.mutate(draft)}
        />
      </Expander>
      {savedNotice && !editing && <Notice tone="success">Configuration saved.</Notice>}

      <Section title="Danger zone">
        <Teardown
          settings={settings}
          disabled={editing || busy}
          onDrop={() =>
            actionMutation.mutate({
              action: "drop",
              targets: settings.ownedObjects.map((o) => o.fqn),
              retry: false,
            })
          }
        />
      </Section>
    </>
  );
}

// Draft state lives here and is discarded whenever edit mode toggles (the parent
// remounts this form), matching reset_setup_draft.
function SetupForm({
  settings,
  editing,
  busy,
  onSave,
}: {
  settings: Settings;
  editing: boolean;
  busy: boolean;
  onSave: (draft: SetupDraft) => void;
}) {
  const [draft, setDraft] = useState<SetupDraft>({
    database: settings.database,
    schema: settings.schema,
    warehouseName: settings.warehouseName,
    warehouseSize: settings.warehouseSize,
    objectPrefix: settings.objectPrefix,
  });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const field = (label: string, key: keyof SetupDraft, help: string) => (
    <div className="flex flex-col gap-1">
      <Label htmlFor={`setup-${key}`}>{label}</Label>
      <Input
        id={`setup-${key}`}
        value={draft[key]}
        disabled={!editing}
        title={help}
        onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
      />
    </div>
  );
  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        const found = validateSetupDraft(draft);
        setErrors(found);
        if (Object.keys(found).length === 0) onSave(draft);
      }}
    >
      <Notice>
        This page creates Snowflake objects (warehouse, aggregate tables, tasks). Your active role
        must have <strong>CREATE TABLE</strong>, <strong>CREATE TASK</strong>, and{" "}
        <strong>CREATE WAREHOUSE</strong> privileges on the target schema. If provisioning fails,
        ask your Snowflake admin to grant these privileges.
      </Notice>
      <Caption>Where acceleration objects (warehouse, aggregate tables) will be created.</Caption>
      {!editing && (
        <Caption>
          Turn on &quot;Edit setup&quot; at the top of the page to change these values.
        </Caption>
      )}
      <div className="grid gap-4 sm:grid-cols-2">
        {field("Database", "database", "Target database for acceleration objects.")}
        {field("Schema", "schema", "Target schema for acceleration objects.")}
        {field(
          "Task warehouse",
          "warehouseName",
          "Standard warehouse used to execute the scheduled refresh tasks.",
        )}
        <div className="flex flex-col gap-1">
          <Label htmlFor="setup-warehouseSize">Warehouse size</Label>
          <select
            id="setup-warehouseSize"
            className="h-8 rounded-lg border border-input bg-transparent px-2 text-sm disabled:opacity-50"
            value={draft.warehouseSize}
            disabled={!editing}
            onChange={(e) => setDraft({ ...draft, warehouseSize: e.target.value })}
          >
            {WAREHOUSE_SIZES.map((size) => (
              <option key={size} value={size}>
                {size}
              </option>
            ))}
          </select>
        </div>
      </div>
      {field(
        "Object name prefix",
        "objectPrefix",
        "All created objects use this prefix (e.g. COST_MGMT_COCO).",
      )}
      {Object.entries(errors).map(([label, message]) => (
        <Notice key={label} tone="error">
          {label}: {message}
        </Notice>
      ))}
      <div>
        <Button type="submit" size="sm" disabled={!editing || busy}>
          Save configuration
        </Button>
      </div>
    </form>
  );
}

function Teardown({
  settings,
  disabled,
  onDrop,
}: {
  settings: Settings;
  disabled: boolean;
  onDrop: () => void;
}) {
  const [open, setOpen] = useState(false);
  if (settings.ownedObjects.length === 0)
    return <Caption>No acceleration objects to tear down.</Caption>;
  return (
    <>
      <Notice tone="warning">
        <strong>{settings.ownedObjects.length} object(s)</strong> will be permanently dropped.
        Dashboard queries will revert to reading ACCOUNT_USAGE directly.
      </Notice>
      <div>
        <Button size="sm" variant="outline" disabled={disabled} onClick={() => setOpen(true)}>
          Tear down all acceleration objects
        </Button>
      </div>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Confirm Teardown</DialogTitle>
            <DialogDescription>
              The following objects will be permanently dropped:
            </DialogDescription>
          </DialogHeader>
          <div className="max-h-72 overflow-auto rounded-md border bg-muted/40 p-2 text-xs">
            {settings.ownedObjects.map((o) => (
              <code key={o.fqn} className="block">
                {o.fqn} [{o.type}]
              </code>
            ))}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={disabled}
              onClick={() => {
                setOpen(false);
                onDrop();
              }}
            >
              Drop all objects
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
