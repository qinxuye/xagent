import React from "react";
import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  TaskRuntimeComposerMenuExtension,
  TaskRuntimeComposerSelectionExtension,
  TaskRuntimeMessageMetadataExtension,
  TaskRuntimeSettingsExtension,
  hasTaskRuntimeComposerExtension,
} from "./task-runtime-ui-extension";

const composerProps = {
  disabled: false,
  selection: null,
  onSelectionChange: vi.fn(),
  onRequestClose: vi.fn(),
};

describe("default task runtime UI extension", () => {
  it("keeps every optional UI slot inert in the OSS build", () => {
    expect(hasTaskRuntimeComposerExtension).toBe(false);

    const { container } = render(
      <>
        <TaskRuntimeComposerMenuExtension {...composerProps} />
        <TaskRuntimeComposerSelectionExtension {...composerProps} />
        <TaskRuntimeSettingsExtension />
        <TaskRuntimeMessageMetadataExtension
          bindings={["local_browser"]}
          publicMetadata={{ local_browser: { kind: "local_browser" } }}
        />
      </>,
    );

    expect(container).toBeEmptyDOMElement();
  });

});
