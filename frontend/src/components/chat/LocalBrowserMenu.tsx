"use client";

import React, { useCallback, useState } from "react";
import {
  Check,
  ChevronRight,
  Laptop,
  Loader2,
  Paperclip,
  Plus,
  X,
} from "lucide-react";

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useI18n } from "@/contexts/i18n-context";
import { apiRequest } from "@/lib/api-wrapper";
import { cn, getApiUrl } from "@/lib/utils";

interface ReadinessIssue {
  code: string;
  message: string;
}

export interface LocalBrowserTarget {
  pid: number;
  window_id: number;
  application: string;
  title?: string | null;
}

interface LocalBrowserReadiness {
  ready: boolean;
  application: string;
  title?: string | null;
  windows: LocalBrowserTarget[];
  issues: ReadinessIssue[];
  message: string;
}

interface LocalBrowserMenuProps {
  disabled: boolean;
  selectedTarget: LocalBrowserTarget | null;
  onTargetChange: (target: LocalBrowserTarget | null) => void;
  onAddFiles?: () => void;
  showLocalBrowser: boolean;
}

export function LocalBrowserMenu({
  disabled,
  selectedTarget,
  onTargetChange,
  onAddFiles,
  showLocalBrowser,
}: LocalBrowserMenuProps) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [showWindowPicker, setShowWindowPicker] = useState(false);
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
        windows: [],
        issues: [],
        message: t("chatPage.input.localBrowser.unavailable"),
      });
    } finally {
      setLoading(false);
    }
  }, [showLocalBrowser, t]);

  const localBrowserDisabled = disabled || loading || readiness?.ready !== true;
  const selected = selectedTarget !== null;
  const status = loading
    ? t("chatPage.input.localBrowser.checking")
    : readiness?.ready
      ? selectedTarget
        ? [selectedTarget.application, selectedTarget.title].filter(Boolean).join(" · ")
        : t("chatPage.input.localBrowser.chooseWindow")
      : readiness?.message || t("chatPage.input.localBrowser.unavailable");

  return (
    <div className="flex min-w-0 items-center gap-1.5">
      <Popover
        open={open}
        onOpenChange={(nextOpen) => {
          setOpen(nextOpen);
          if (!nextOpen) setShowWindowPicker(false);
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
            <Popover
              open={showWindowPicker}
              onOpenChange={(nextOpen) => {
                setShowWindowPicker(nextOpen);
                if (nextOpen) void refreshReadiness();
              }}
            >
              <PopoverTrigger asChild>
                <button
                  type="button"
                  className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left transition-colors hover:bg-muted"
                >
                  <Laptop className="h-4 w-4 shrink-0" />
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-medium">
                      {t("chatPage.input.localBrowser.label")}
                    </span>
                    {selectedTarget && (
                      <span className="block truncate text-xs text-muted-foreground">
                        {[selectedTarget.application, selectedTarget.title]
                          .filter(Boolean)
                          .join(" · ")}
                      </span>
                    )}
                  </span>
                  <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
                </button>
              </PopoverTrigger>
              <PopoverContent
                align="start"
                side="right"
                sideOffset={8}
                className="w-80 space-y-1 p-1.5"
              >
                <p className="px-2 py-1.5 text-xs font-medium text-muted-foreground">
                  {t("chatPage.input.localBrowser.chooseWindow")}
                </p>
                <p className="px-2 pb-1 text-xs leading-relaxed text-muted-foreground">
                  {t("chatPage.input.localBrowser.controlScope")}
                </p>
                <div className="max-h-64 space-y-0.5 overflow-y-auto px-1 pb-1">
                  {loading ? (
                    <div className="flex items-center gap-3 px-2 py-2 text-sm text-muted-foreground">
                      <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
                      <span>{t("chatPage.input.localBrowser.checking")}</span>
                    </div>
                  ) : readiness?.ready && readiness.windows.length > 0 ? (
                    readiness.windows.map((window) => {
                      const isSelected = selectedTarget?.pid === window.pid
                        && selectedTarget?.window_id === window.window_id;
                      return (
                        <button
                          type="button"
                          key={`${window.pid}:${window.window_id}`}
                          disabled={localBrowserDisabled}
                          onClick={() => {
                            onTargetChange(window);
                            setShowWindowPicker(false);
                            setOpen(false);
                          }}
                          className={cn(
                            "flex w-full items-center gap-2 rounded-md px-2 py-2 text-left hover:bg-muted",
                            isSelected && "bg-primary/5",
                          )}
                        >
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-sm">{window.application}</span>
                            {window.title && (
                              <span className="block truncate text-xs text-muted-foreground">
                                {window.title}
                              </span>
                            )}
                          </span>
                          {isSelected && <Check className="h-4 w-4 shrink-0 text-primary" />}
                        </button>
                      );
                    })
                  ) : (
                    <p className="px-2 py-2 text-sm text-muted-foreground">
                      {readiness?.message || t("chatPage.input.localBrowser.unavailable")}
                    </p>
                  )}
                </div>
              </PopoverContent>
            </Popover>
          )}
        </PopoverContent>
      </Popover>

      {selected && (
        <div
          className="inline-flex h-8 min-w-0 max-w-[180px] items-center gap-1.5 rounded-lg border border-border bg-secondary/70 px-2.5 text-xs text-foreground"
          title={status}
        >
          <Laptop className="h-3.5 w-3.5 shrink-0" />
          <span className="truncate">{t("chatPage.input.localBrowser.label")}</span>
          <span
            role="status"
            aria-label={status}
            className={cn(
              "h-2 w-2 shrink-0 rounded-full",
              readiness?.ready
                ? "bg-emerald-500"
                : readiness
                  ? "bg-amber-500"
                  : "bg-muted-foreground/35",
            )}
          />
          <button
            type="button"
            aria-label={t("common.remove")}
            title={t("common.remove")}
            className="ml-0.5 rounded-sm p-0.5 hover:bg-foreground/10"
            onClick={() => onTargetChange(null)}
          >
            <X className="h-3 w-3" />
          </button>
        </div>
      )}
    </div>
  );
}
