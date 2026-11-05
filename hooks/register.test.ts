import { expect, test } from 'claude-code/testing'

// Nothing sits beneath the plugin: these hooks stand for the engine and for tracker.py.
test('logs the answer, then shows the status line; /cc-usage runs the report', async ($, on) => {
  const runs: { args: string[]; stdin?: string }[] = []
  const statuses: (string | undefined)[] = []
  on('process.run', (_, e) => {
    const args = e.argv.slice(2)
    runs.push({ args: [...args], stdin: e.init?.stdin as string | undefined })
    const stdout = args[0] === 'statusline' ? 'Last answer 0.50% · Session 2.00% of 5h\n'
      : args[0] === 'report' ? `report ${args[1] ?? 15}\n` : ''

    return { value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('ui.status', (_, e) => (statuses.push(e.text), { value: undefined }))
  on('classic.Stop', () => ({}))
  on('command.run', () => ({ text: 'unanswered' }))

  await $.classic.Stop({ session_id: 's1', transcript_path: '/t.jsonl', cwd: '/', stop_hook_active: false })

  expect(runs.map(r => r.args[0])).toEqual(['stop', 'statusline'])
  expect(JSON.parse(runs[0].stdin ?? '{}').session_id).toBe('s1')
  expect(JSON.parse(runs[1].stdin ?? '{}')).toEqual({ session_id: 's1' })
  expect(statuses).toEqual(['Last answer 0.50% · Session 2.00% of 5h'])

  const report = await $.command.run({ command: 'cc-usage', args: '40' })
  expect(report.text).toBe('report 40')
})

test('a Stop raised by a Stop hook is not logged again', async ($, on) => {
  const runs: string[] = []
  on('process.run', (_, e) => (runs.push(e.argv[2]), {
    value: { exitCode: 0, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false },
  }))
  on('classic.Stop', () => ({}))

  await $.classic.Stop({ session_id: 's1', transcript_path: '/t.jsonl', cwd: '/', stop_hook_active: true })

  expect(runs).toEqual([])
})
