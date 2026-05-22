import { describe, expect, it } from "vitest"

import {
  getFinalAnswerStreamActionPayload,
  getFinalAnswerStreamMessageId,
  mergeTraceEventsById,
} from "@/lib/streaming-final-answer"

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

  it("preserves streaming message trace events when final content replaces the message", () => {
    const toolStart = {
      event_id: "tool-start",
      event_type: "tool_execution_start",
    }
    const toolEnd = {
      event_id: "tool-end",
      event_type: "tool_execution_end",
    }

    expect(
      mergeTraceEventsById([toolStart, toolEnd], [], [toolEnd]),
    ).toEqual([toolStart, toolEnd])
  })

  it("marks error events as failed terminal stream updates", () => {
    const payload = getFinalAnswerStreamActionPayload({
      eventType: "final_answer_error",
      eventData: {
        message_id: "final_answer_1",
        error: "provider disconnected",
      },
      timestamp: "2026-05-20T12:00:01.000Z",
    })

    expect(payload).toEqual({
      messageId: "final_answer_1",
      content: "provider disconnected",
      status: "failed",
      timestamp: "2026-05-20T12:00:01.000Z",
    })
  })

  it("extracts stream message id from authoritative final payloads", () => {
    expect(
      getFinalAnswerStreamMessageId({
        result: { stream_message_id: "final_answer_1" },
      }),
    ).toBe("final_answer_1")
    expect(
      getFinalAnswerStreamMessageId({
        stream_message_id: "final_answer_2",
      }),
    ).toBe("final_answer_2")
  })
})
