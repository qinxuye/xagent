import React, { useEffect, useState } from 'react'
import { useFileAccess } from '@/contexts/file-access-context'
import { useI18n } from '@/contexts/i18n-context'

type Status = 'valid' | 'invalid' | 'unchecked'
type DisplayReport = { status: Status; message: string }

async function readReport(response: Response): Promise<DisplayReport> {
  if (!response.ok) throw new Error('Validation unavailable')
  // An older backend may ignore validation_only and return the attachment
  // itself. Never interpret arbitrary JSON file contents as a report.
  if (response.headers.get('content-type')?.split(';')[0] !== 'application/vnd.xagent.validation+json') {
    throw new Error('Not a validation response')
  }
  const data = await response.json()
  const statuses = ['valid', 'invalid', 'unchecked']
  if (!data || !statuses.includes(data.status) ||
      !Array.isArray(data.checks) || !data.checks.length ||
      !data.checks.every((c: { status?: unknown } | null) => c && statuses.includes(String(c.status)))) {
    throw new Error('Invalid report')
  }
  if (data.status !== 'unchecked' &&
      (typeof data.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(data.sha256))) {
    throw new Error('Missing snapshot')
  }
  if (data.status === 'valid' && !data.checks.every((c: { status: string }) => c.status === 'valid')) {
    throw new Error('Incomplete checks')
  }
  const message = data.checks
    .filter((c: { status: string; message?: unknown }) => c.status !== 'valid' && typeof c.message === 'string')
    .map((c: { message: string }) => c.message).join(' ')
  return { status: data.status, message }
}

/** Server-authoritative, current-byte checks, independent of preview renderers. */
export function ArtifactValidation({ fileId, children }: {
  fileId: string
  children: React.ReactNode
}) {
  const policy = useFileAccess()
  const { t } = useI18n()
  const [attempt, setAttempt] = useState(0)
  const [result, setResult] = useState<DisplayReport & { key: string }>()
  const url = policy.validationUrl?.(fileId)
  const key = `${url}:${attempt}`

  useEffect(() => {
    if (!url) return
    let active = true
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 20_000)
    const check = async () => {
      try {
        const response = await policy.request(url, { signal: controller.signal, cache: 'no-store' })
        const report = await readReport(response)
        if (active) setResult({ key, ...report })
      } catch {
        if (active) setResult({ key, status: 'unchecked', message: '' })
      } finally {
        clearTimeout(timeout)
      }
    }
    void check()
    return () => {
      active = false
      clearTimeout(timeout)
      controller.abort()
    }
  }, [key, url, policy])

  if (!url) return <>{children}</>
  const current = result?.key === key ? result : undefined
  const label = current?.status ?? 'checking'
  return (
    <div data-artifact-validation={label}>
      <div className="flex items-center gap-2 py-1 text-xs text-muted-foreground" role="status">
        <span className={label === 'invalid' ? 'text-destructive' : undefined}>
          {t(`files.validation.${label}`)}
        </span>
        {current ? (
          <button type="button" className="underline" onClick={() => setAttempt(n => n + 1)}>
            {t('files.validation.recheck')}
          </button>
        ) : null}
      </div>
      {current?.message ? <p className="text-xs text-muted-foreground">{current.message}</p> : null}
      <React.Fragment key={key}>{children}</React.Fragment>
    </div>
  )
}
