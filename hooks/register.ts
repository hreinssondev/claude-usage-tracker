import type { EngineInterface, Register } from 'claude-code'

// tracker.py does the work, exactly as the settings.json install runs it:
// `stop` after each answer, `statusline` for the line, `report` for /cc-usage.
const REFRESH_MS = 60_000 // the 5h reset time and "new window" move with the clock

function tracker($: EngineInterface, args: string[], stdin = '', timeoutMs = 30_000) {
  return $.process.run(['python3', `${$.plugin.root}/tracker.py`, ...args], { stdin, timeoutMs })
}

async function refresh($: EngineInterface, sessionId: string) {
  const { exitCode, stdout } = await tracker($, ['statusline'], JSON.stringify({ session_id: sessionId }), 5_000)
  if (exitCode === 0 && stdout.trim()) $.ui.status(stdout.trim())
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const started = await next(e)
    await $.command.register({
      name: 'cc-usage',
      description: 'Usage report: current limits, recent answers, per-session totals',
      argumentHint: '[answers]',
    })
    const sessionId = await $.session.id()
    void refresh($, sessionId).catch(() => {})
    $.clock.every(REFRESH_MS, () => void refresh($, sessionId).catch(() => {}))

    return started
  })

  on('classic.Stop', async ($, e, next) => {
    const stopped = await next(e)
    if (!e.stop_hook_active) {
      await tracker($, ['stop'], JSON.stringify(e)).catch(() => undefined)
      await refresh($, e.session_id).catch(() => {})
    }

    return stopped
  })

  on('command.run', { command: 'cc-usage' }, async ($, e) => {
    const n = e.args.trim()
    const { exitCode, stdout, stderr } = await tracker($, /^\d+$/.test(n) ? ['report', n] : ['report'])

    return { text: exitCode === 0 ? stdout.trimEnd() : `cc-usage failed: ${stderr.trim() || `exit ${exitCode}`}` }
  })
}
