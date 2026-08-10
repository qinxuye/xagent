import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiRequestMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api-wrapper", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api-wrapper")>(
    "@/lib/api-wrapper",
  );
  return { ...actual, apiRequest: apiRequestMock };
});

vi.mock("@/lib/utils", () => ({
  cn: (...classes: Array<string | false | null | undefined>) =>
    classes.filter(Boolean).join(" "),
  generateClientMessageId: () => "client-message-runtime",
  getApiUrl: () => "http://api.local",
  getUploadApiUrl: () => "http://upload.local",
}));

vi.mock("@/contexts/i18n-context", () => ({
  useI18n: () => ({ t: (key: string) => key }),
}));

vi.mock("@/contexts/app-context-chat", () => ({
  useApp: () => ({ openFilePreview: vi.fn() }),
}));

vi.mock("@/contexts/auth-context", () => ({
  useAuth: () => ({ user: { id: "2", is_admin: false } }),
}));

vi.mock("@/components/config-dialog", () => ({
  ConfigDialog: ({ trigger }: { trigger: unknown }) => trigger,
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/hooks/use-file-mention", () => ({
  useFileMention: () => ({
    checkTrigger: vi.fn(),
    dropdownPosition: null,
    fileList: [],
    filteredFiles: [],
    handleKeyDown: vi.fn(() => false),
    insertFile: vi.fn(),
    isLoadingFiles: false,
    resetMention: vi.fn(),
    selectedFileIndex: 0,
    showFilePicker: false,
  }),
}));

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}));

vi.mock("@/lib/task-runtime-ui-extension", async () => {
  const ReactModule = await vi.importActual<typeof import("react")>("react");
  return {
    hasTaskRuntimeComposerExtension: true,
    TaskRuntimeComposerMenuExtension: ({
      disabled,
      onSelectionChange,
      onRequestClose,
    }: {
      disabled: boolean;
      onSelectionChange: (selection: unknown) => void;
      onRequestClose: () => void;
    }) => ReactModule.createElement("button", {
      type: "button",
      disabled,
      onClick: () => {
        onSelectionChange({
          runtimeExtensions: { browser_relay: { target: "approved_tab" } },
        });
        onRequestClose();
      },
    }, "My browser"),
    TaskRuntimeComposerSelectionExtension: ({ selection }: {
      selection: unknown;
    }) => selection
      ? ReactModule.createElement("span", null, "My browser selected")
      : null,
  };
});

import { ChatInput } from "./ChatInput";

describe("ChatInput task runtime UI extension", () => {
  beforeEach(() => {
    apiRequestMock.mockReset();
    apiRequestMock.mockImplementation(() => Promise.resolve(
      new Response(JSON.stringify([]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ));
  });

  it("submits a distribution-provided runtime without exposing local browser", async () => {
    const onSend = vi.fn();
    const { container } = render(
      <ChatInput
        hideFileUpload
        inputValue="inspect my signed-in tab"
        onInputChange={vi.fn()}
        onSend={onSend}
        taskConfig={{ model: "model-1" }}
      />,
    );

    fireEvent.click(screen.getByLabelText("chatPage.input.actions.add"));
    expect(screen.queryByText("chatPage.input.localBrowser.label")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "My browser" }));
    expect(screen.getByText("My browser selected")).toBeInTheDocument();

    fireEvent.submit(container.querySelector("form") as HTMLFormElement);

    await waitFor(() => expect(onSend).toHaveBeenCalledWith(
      "inspect my signed-in tab",
      expect.objectContaining({
        runtimeExtensions: {
          browser_relay: { target: "approved_tab" },
        },
      }),
    ));
  });
});
