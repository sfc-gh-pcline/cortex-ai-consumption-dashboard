"use client";

import type { ReactNode } from "react";

import { Caption, Notice } from "@/components/kit/notice";
import { DateControls } from "@/components/shell/date-controls";
import { Sidebar } from "@/components/shell/sidebar";
import { DashboardProvider, useDashboard } from "@/lib/client/dashboard";
import { preflightErrors, preflightWarnings } from "@/lib/preflight";

function Frame({ children }: { children: ReactNode }) {
  const { preflight, preflightError } = useDashboard();
  const errors = preflight ? preflightErrors(preflight.preflight) : [];
  const warnings = preflight ? preflightWarnings(preflight.preflight) : [];
  return (
    <div className="flex min-h-screen">
      <Sidebar />
      <main className="flex min-w-0 flex-1 flex-col gap-6 px-8 py-6">
        {preflightError && (
          <Notice tone="error">Cannot reach the dashboard API: {preflightError}</Notice>
        )}
        {errors.map((check) => (
          <Notice key={check.name} tone="error">
            <strong>Setup required — {check.name}:</strong> {check.detail} {check.remediation}
          </Notice>
        ))}
        {warnings.map((check) => (
          <Notice key={check.name} tone="warning">
            <strong>{check.name}:</strong> {check.detail} {check.remediation}
          </Notice>
        ))}
        {!preflight && !preflightError && <Caption>Checking the Snowflake connection...</Caption>}
        {preflight && errors.length === 0 && (
          <>
            <DateControls />
            {children}
          </>
        )}
      </main>
    </div>
  );
}

// Root layout body: sidebar, preflight results, date controls, then the page.
export function DashboardShell({ children }: { children: ReactNode }) {
  return (
    <DashboardProvider>
      <Frame>{children}</Frame>
    </DashboardProvider>
  );
}
