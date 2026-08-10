import type { ComponentType } from "react";

export type TaskRuntimeExtensionConfiguration = Record<
  string,
  Record<string, unknown>
>;

export interface TaskRuntimeComposerSelection {
  runtimeExtensions: TaskRuntimeExtensionConfiguration;
}

export interface TaskRuntimeComposerExtensionProps {
  disabled: boolean;
  selection: TaskRuntimeComposerSelection | null;
  onSelectionChange: (
    selection: TaskRuntimeComposerSelection | null,
  ) => void;
  onRequestClose: () => void;
}

export interface TaskRuntimeMessageMetadataExtensionProps {
  bindings: readonly string[];
  publicMetadata: Readonly<Record<string, Record<string, unknown>>>;
}

/**
 * Build-time extension points for distributions that provide additional
 * task-scoped runtimes. The OSS implementation is deliberately inert. A
 * composed distribution may replace this module without overriding the
 * composer, Settings page, or conversation panel themselves.
 */
export const hasTaskRuntimeComposerExtension = false;

export const TaskRuntimeComposerMenuExtension: ComponentType<
  TaskRuntimeComposerExtensionProps
> = () => null;

export const TaskRuntimeComposerSelectionExtension: ComponentType<
  TaskRuntimeComposerExtensionProps
> = () => null;

export const TaskRuntimeSettingsExtension: ComponentType = () => null;

export const TaskRuntimeMessageMetadataExtension: ComponentType<
  TaskRuntimeMessageMetadataExtensionProps
> = () => null;
