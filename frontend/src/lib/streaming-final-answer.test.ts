import { describe, expect, it } from "vitest"

import { getFinalAnswerStreamActionPayload } from "@/lib/streaming-final-answer"

describe("streaming final answer events", () => {
  it("reads websocket final-answer fields from nested data payloads", () => {
    const payload = getFinalAnswerStreamActionPayload({
      eventType: "final_answer_delta",
      eventData: {
        type: "final_answer_delta",
        data: {
          message_id: "final_answer_1",
          delta: "hello",
        },
      },
      timestamp: "2026-05-20T12:00:00.000Z",
    })

    expect(payload).toEqual({
      messageId: "final_answer_1",
      delta: "hello",
      status: "running",
      timestamp: "2026-05-20T12:00:00.000Z",
    })
  })
})
