import { describe, expect, it } from "vitest"

import type { Model } from "./models"
import { guessProfile } from "./auto-model-config-card"

const model: Model = {
  id: 1,
  model_id: "saved-gpt",
  category: "llm",
  model_provider: "openai",
  model_name: "gpt-5.5",
  is_active: true,
  is_owner: true,
  can_edit: true,
  can_delete: true,
  is_shared: false,
}

describe("guessProfile", () => {
  it("matches a profile when aliases are missing or null", () => {
    expect(
      guessProfile(model, [
        { id: "other/model", aliases: null, input_modalities: ["text"] },
        { id: "openai/gpt-5.5", input_modalities: ["text"] },
      ]),
    ).toBe("openai/gpt-5.5")
  })
})
