"use client"

import { useState, useEffect } from "react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Separator } from "@/components/ui/separator"
import {
  ChevronDown,
  ChevronRight,
  Loader2,
  CheckCircle,
  XCircle,
  Clock,
  Brain,
  Search,
  Database,
  Table,
  FileText,
  Code,
  Zap
} from "lucide-react"
import { useI18n } from "@/contexts/i18n-context"

interface ThinkingStep {
  id: string
  name: string
  description: string
  status: "pending" | "running" | "completed" | "failed"
  type: "planning" | "analysis" | "sql_generation" | "execution" | "result"
  started_at?: string | number
  completed_at?: string | number
  dependencies?: string[]
  details?: {
    content?: string
    sql_query?: string
    result_data?: any
    error_message?: string
  }
  tool_names?: string[]
}

interface ThinkingTimelineProps {
  steps: ThinkingStep[]
  isComplete: boolean
  onAutoCollapse?: () => void
}

const stepTypeConfig = {
  planning: {
    icon: Brain,
    labelKey: "agentStore.text2sql.timeline.stepType.planning",
    color: "text-purple-500",
    bgColor: "bg-purple-50 dark:bg-purple-900/20"
  },
  analysis: {
    icon: Search,
    labelKey: "agentStore.text2sql.timeline.stepType.analysis",
    color: "text-blue-500",
    bgColor: "bg-blue-50 dark:bg-blue-900/20"
  },
  sql_generation: {
    icon: Code,
    labelKey: "agentStore.text2sql.timeline.stepType.sql_generation",
    color: "text-green-500",
    bgColor: "bg-green-50 dark:bg-green-900/20"
  },
  execution: {
    icon: Database,
    labelKey: "agentStore.text2sql.timeline.stepType.execution",
    color: "text-orange-500",
    bgColor: "bg-orange-50 dark:bg-orange-900/20"
  },
  result: {
    icon: Table,
    labelKey: "agentStore.text2sql.timeline.stepType.result",
    color: "text-teal-500",
    bgColor: "bg-teal-50 dark:bg-teal-900/20"
  }
}

const statusConfig = {
  pending: {
    icon: Clock,
    color: "text-gray-400",
    labelKey: "agentStore.text2sql.timeline.status.pending"
  },
  running: {
    icon: Loader2,
    color: "text-blue-500 animate-spin",
    labelKey: "agentStore.text2sql.timeline.status.running"
  },
  completed: {
    icon: CheckCircle,
    color: "text-green-500",
    labelKey: "agentStore.text2sql.timeline.status.completed"
  },
  failed: {
    icon: XCircle,
    color: "text-red-500",
    labelKey: "agentStore.text2sql.timeline.status.failed"
  }
}

export function ThinkingTimeline({ steps, isComplete, onAutoCollapse }: ThinkingTimelineProps) {
  const { t } = useI18n()
  const [expandedSteps, setExpandedSteps] = useState<Set<string>>(new Set())
  const [autoCollapseTimer, setAutoCollapseTimer] = useState<NodeJS.Timeout | null>(null)

  // Auto expand running steps
  useEffect(() => {
    const runningSteps = steps.filter(step => step.status === "running")
    if (runningSteps.length > 0) {
      setExpandedSteps(prev => {
        const newExpanded = new Set(prev)
        runningSteps.forEach(step => {
          newExpanded.add(step.id)
        })
        return newExpanded
      })
    }
  }, [steps])

  // Auto collapse after completion
  useEffect(() => {
    if (isComplete && onAutoCollapse) {
      // Auto collapse after 3 seconds
      const timer = setTimeout(() => {
        setExpandedSteps(new Set())
        onAutoCollapse()
      }, 3000)

      setAutoCollapseTimer(timer)

      return () => {
        if (timer) clearTimeout(timer)
      }
    }
  }, [isComplete, onAutoCollapse])

  const toggleStep = (stepId: string) => {
    setExpandedSteps(prev => {
      const newExpanded = new Set(prev)
      if (newExpanded.has(stepId)) {
        newExpanded.delete(stepId)
      } else {
        newExpanded.add(stepId)
      }
      return newExpanded
    })
  }

  const cancelAutoCollapse = () => {
    if (autoCollapseTimer) {
      clearTimeout(autoCollapseTimer)
      setAutoCollapseTimer(null)
    }
  }

  const formatDuration = (startedAt?: string | number, completedAt?: string | number) => {
    if (!startedAt) return ""
    const start = typeof startedAt === "string" ? new Date(startedAt).getTime() : startedAt
    const end = completedAt ? (typeof completedAt === "string" ? new Date(completedAt).getTime() : completedAt) : Date.now()
    const duration = Math.round((end - start) / 1000)
    return duration > 0 ? `${duration}s` : ""
  }

  const getStepIcon = (step: ThinkingStep) => {
    console.log('Step icon debug:', { stepId: step.id, type: step.type, status: step.status })
    const StatusIcon = statusConfig[step.status]?.icon || Clock
    const stepTypeConfigItem = stepTypeConfig[step.type] || stepTypeConfig.planning // Default to planning type
    const TypeIcon = stepTypeConfigItem.icon

    return (
      <div className="relative">
        <div className={`w-8 h-8 rounded-full flex items-center justify-center ${stepTypeConfigItem.bgColor}`}>
          <TypeIcon className={`h-4 w-4 ${stepTypeConfigItem.color}`} />
        </div>
        <div className="absolute -bottom-1 -right-1 w-4 h-4 rounded-full bg-white border-2 border-white flex items-center justify-center">
          <StatusIcon className={`h-3 w-3 ${statusConfig[step.status]?.color || 'text-gray-500'}`} />
        </div>
      </div>
    )
  }

  const renderStepDetails = (step: ThinkingStep) => {
    if (!expandedSteps.has(step.id)) return null

    return (
      <div className="mt-3 p-3 bg-muted/30 rounded-md space-y-2">
        {step.description && (
          <p className="text-sm text-muted-foreground">{step.description}</p>
        )}

        {step.details?.sql_query && (
          <div className="space-y-1">
            <p className="text-xs font-medium text-muted-foreground">{t('agentStore.text2sql.timeline.details.sql')}</p>
            <pre className="text-xs bg-background p-2 rounded border overflow-x-auto">
              {step.details.sql_query}
            </pre>
          </div>
        )}

        {step.details?.result_data && (
          <div className="space-y-1">
            <p className="text-xs font-medium text-muted-foreground">{t('agentStore.text2sql.timeline.details.result')}</p>
            <div className="text-xs bg-background p-2 rounded border max-h-32 overflow-auto">
              {typeof step.details.result_data === 'string'
                ? step.details.result_data
                : JSON.stringify(step.details.result_data, null, 2)
              }
            </div>
          </div>
        )}

        {step.details?.error_message && (
          <div className="space-y-1">
            <p className="text-xs font-medium text-red-500">{t('agentStore.text2sql.timeline.details.error')}</p>
            <p className="text-xs text-red-400 bg-red-50 dark:bg-red-900/20 p-2 rounded">
              {step.details.error_message}
            </p>
          </div>
        )}

        {step.tool_names && step.tool_names.length > 0 && (
          <div className="flex flex-wrap items-center gap-1">
            <span className="text-xs text-muted-foreground">
              {t('agent.layout.center.labels.suggestedTools')}
            </span>
            {step.tool_names.map((tool, index) => (
              <Badge key={index} variant="secondary" className="text-xs">
                {tool}
              </Badge>
            ))}
          </div>
        )}
      </div>
    )
  }

  if (steps.length === 0) return null

  return (
    <Card className="w-full">
      <CardContent className="p-4 w-full">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Brain className="h-5 w-5 text-purple-500" />
            <h3 className="font-semibold">{t('agentStore.text2sql.timeline.title')}</h3>
            {isComplete && (
              <Badge variant="outline" className="text-green-600 border-green-600">
                {t('agentStore.text2sql.timeline.status.completed')}
              </Badge>
            )}
          </div>
          {autoCollapseTimer && (
            <Button
              variant="ghost"
              size="sm"
              onClick={cancelAutoCollapse}
              className="text-xs"
              title={t('agentStore.text2sql.timeline.actions.cancelAutoCollapse')}
              aria-label={t('agentStore.text2sql.timeline.actions.cancelAutoCollapse')}
            >
              {t('agentStore.text2sql.timeline.actions.cancelAutoCollapse')}
            </Button>
          )}
        </div>

        <div className="w-full">
          <div className="space-y-4">
            {steps.map((step, index) => {
              const isExpanded = expandedSteps.has(step.id)
              const hasDependencies = step.dependencies && step.dependencies.length > 0

              return (
                <div key={step.id} className="relative">
                  {/* Connection Line */}
                  {index < steps.length - 1 && (
                    <div className="absolute left-4 top-12 w-0.5 h-8 bg-border" />
                  )}

                  <div className="flex gap-3">
                    {/* Step Icon */}
                    <div className="flex-shrink-0">
                      {getStepIcon(step)}
                    </div>

                    {/* Step Content */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2 flex-1">
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => toggleStep(step.id)}
                            className="h-auto p-1 font-normal text-left flex-1 justify-start"
                            title={t('agentStore.text2sql.timeline.actions.toggleStep', { name: step.name })}
                            aria-label={t('agentStore.text2sql.timeline.actions.toggleStep', { name: step.name })}
                          >
                            <div className="flex items-center gap-1">
                              {isExpanded ? (
                                <ChevronDown className="h-4 w-4" />
                              ) : (
                                <ChevronRight className="h-4 w-4" />
                              )}
                              <span className="text-sm font-medium">{step.name}</span>
                            </div>
                          </Button>

                          <Badge variant="outline" className="text-xs">
                            {t((stepTypeConfig[step.type] || stepTypeConfig.planning).labelKey)}
                          </Badge>

                          {hasDependencies && (
                            <Badge variant="secondary" className="text-xs">
                              {t('agentStore.text2sql.timeline.labels.dependencies', { count: step.dependencies?.length ?? 0 })}
                            </Badge>
                          )}
                        </div>

                        <div className="flex items-center gap-2 text-xs text-muted-foreground">
                          <span>{t(statusConfig[step.status]?.labelKey || 'agentStore.text2sql.timeline.labels.unknownStatus')}</span>
                          {formatDuration(step.started_at, step.completed_at) && (
                            <span>({formatDuration(step.started_at, step.completed_at)})</span>
                          )}
                        </div>
                      </div>

                      {/* Step Details */}
                      {renderStepDetails(step)}

                      {/* Dependency Hint */}
                      {hasDependencies && index > 0 && (
                        <div className="mt-2 text-xs text-muted-foreground">
                          <p>{t('agentStore.text2sql.timeline.labels.basedOnSteps', { steps: step.dependencies?.join(", ") ?? "" })}</p>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
