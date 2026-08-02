"use client";

import React, { useCallback, useState } from "react";
import { Check, Laptop, Loader2, Paperclip, Plus } from "lucide-react";

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useI18n } from "@/contexts/i18n-context";
import { apiRequest } from "@/lib/api-wrapper";
import { cn, getApiUrl } from "@/lib/utils";

interface ReadinessIssue {
  code: string;
  message: string;
}

interface LocalBrowserReadiness {
  ready: boolean;
  application: string;
  title?: string | null;
  issues: ReadinessIssue[];
  message: string;
}

interface LocalBrowserMenuProps {
  disabled: boolean;
  selected: boolean;
  onSelectedChange: (selected: boolean) => void;
  onAddFiles?: () => void;
  showLocalBrowser: boolean;
}

export function LocalBrowserMenu({
  disabled,
  selected,
  onSelectedChange,
  onAddFiles,
  showLocalBrowser,
}: LocalBrowserMenuProps) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [readiness, setReadiness] = useState<LocalBrowserReadiness | null>(null);

  const refreshReadiness = useCallback(async () => {
    if (!showLocalBrowser) return;
    setLoading(true);
    try {
      const response = await apiRequest(
        `${getApiUrl()}/api/computer/local-browser/readiness`,
        { cache: "no-store" },
      );
      if (!response.ok) throw new Error("readiness request failed");
      setReadiness(await response.json());
    } catch {
      setReadiness({
        ready: false,
        application: "Local browser",
        issues: [],
        message: t("chatPage.input.localBrowser.unavailable"),
      });
    } finally {
      setLoading(false);
    }
  }, [showLocalBrowser, t]);

  const localBrowserDisabled = disabled || loading || readiness?.ready !== true;
  const status = loading
    ? t("chatPage.input.localBrowser.checking")
    : readiness?.ready
      ? [readiness.application, readiness.title].filter(Boolean).join(" · ")
      : readiness?.message || t("chatPage.input.localBrowser.unavailable");

  return (
    <Popover
      open={open}
      onOpenChange={(nextOpen) => {
        setOpen(nextOpen);
        if (nextOpen && showLocalBrowser) void refreshReadiness();
      }}
    >
      <PopoverTrigger
        type="button"
        className="inline-flex h-9 w-9 items-center justify-center rounded-full p-0 text-muted-foreground hover:bg-secondary/80 hover:text-foreground disabled:pointer-events-none disabled:opacity-50"
        disabled={disabled}
        title={t("chatPage.input.actions.add")}
        aria-label={t("chatPage.input.actions.add")}
      >
        <Plus className="h-4 w-4" />
      </PopoverTrigger>
      <PopoverContent align="start" side="top" className="w-80 space-y-1 p-1.5">
        {onAddFiles && (
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              onAddFiles();
            }}
            className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm transition-colors hover:bg-muted"
          >
            <Paperclip className="h-4 w-4 shrink-0" />
            <span>{t("chatPage.input.actions.upload")}</span>
          </button>
        )}
        {showLocalBrowser && (
          <button
            type="button"
            disabled={localBrowserDisabled}
            onClick={() => {
              onSelectedChange(!selected);
              setOpen(false);
            }}
            className={cn(
              "flex w-full items-start gap-3 rounded-lg px-3 py-2 text-left transition-colors",
              localBrowserDisabled
                ? "cursor-not-allowed opacity-55"
                : "hover:bg-muted",
              selected && "bg-primary/5",
            )}
          >
            {loading ? (
              <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin" />
            ) : (
              <Laptop className="mt-0.5 h-4 w-4 shrink-0" />
            )}
            <span className="min-w-0 flex-1">
              <span className="block text-sm font-medium">
                {t("chatPage.input.localBrowser.label")}
              </span>
              <span className="block truncate text-xs text-muted-foreground" title={status}>
                {status}
              </span>
            </span>
            {selected && <Check className="mt-0.5 h-4 w-4 shrink-0 text-primary" />}
          </button>
        )}
      </PopoverContent>
    </Popover>
  );
}
