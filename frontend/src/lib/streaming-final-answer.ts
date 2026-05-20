import { unwrapFinalAnswerContent } from "@/lib/final-answer"

type ResultMessageLike = {
  id: string
  role: string
  isResult?: boolean
}

export type FinalAnswerStreamEventType =
  | "final_answer_start"
  | "final_answer_delta"
  | "final_answer_end"

export type FinalAnswerStreamActionPayload = {
  messageId: string
  delta?: string
  content?: string
  status: "running" | "completed"
  timestamp: string
}

export const isStreamingFinalAnswerMessage = (message: ResultMessageLike): boolean => {
  return (
    message.role === "assistant" &&
    message.isResult === true &&
    message.id.startsWith("final_answer_")
  )
}

export const isFinalAnswerStreamEventType = (
  value: unknown,
): value is FinalAnswerStreamEventType => {
  return (
    value === "final_answer_start" ||
    value === "final_answer_delta" ||
    value === "final_answer_end"
  )
}

export const getFinalAnswerStreamActionPayload = ({
  eventType,
  eventData,
  eventId,
  timestamp,
  fallbackMessageId,
}: {
  eventType: FinalAnswerStreamEventType
  eventData: unknown
  eventId?: unknown
  timestamp?: unknown
  fallbackMessageId?: string
}): FinalAnswerStreamActionPayload | null => {
  const data =
    eventData && typeof eventData === "object"
      ? (eventData as Record<string, unknown>)
      : {}
  const messageId = String(data.message_id || eventId || fallbackMessageId || "")
  if (!messageId) {
    return null
  }

  const normalizedTimestamp = String(timestamp || new Date().toISOString())
  if (eventType === "final_answer_start") {
    return {
      messageId,
      timestamp: normalizedTimestamp,
      status: "running",
    }
  }
  if (eventType === "final_answer_delta") {
    const delta = typeof data.delta === "string" ? data.delta : ""
    if (!delta) {
      return null
    }
    return {
      messageId,
      delta,
      timestamp: normalizedTimestamp,
      status: "running",
    }
  }

  const content =
    typeof data.content === "string" ? unwrapFinalAnswerContent(data.content) : ""
  return {
    messageId,
    content,
    timestamp: normalizedTimestamp,
    status: "completed",
  }
}
