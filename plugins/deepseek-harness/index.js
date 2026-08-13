/**
 * All in Luna's model-facing DeepSeek Harness integration.
 *
 * The plugin intentionally owns only the DSH tool surface.  It invokes the
 * public All in Luna CLI and returns its JSON receipt unchanged, so workflow
 * semantics, durable state, and host-action authority remain in Python.
 */
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { defineTool } from '@deepseek-ai/dsh-tools'

const execFileAsync = promisify(execFile)

export const name = 'allinflash'
export const inject = ['tools']

function nonEmpty(value, field) {
  if (typeof value !== 'string' || value.trim() === '') {
    throw new Error(`${field} must be a non-empty string`)
  }
  return value.trim()
}

function commandConfig(config = {}) {
  const command = nonEmpty(config.command ?? 'allinluna', 'command')
  const commandArgs = Array.isArray(config.commandArgs) ? config.commandArgs.map(String) : []
  const db = typeof config.db === 'string' && config.db.trim() ? config.db.trim() : undefined
  const cwd = typeof config.cwd === 'string' && config.cwd.trim() ? config.cwd.trim() : undefined
  const timeoutMs = Number.isSafeInteger(config.timeoutMs) && config.timeoutMs > 0
    ? config.timeoutMs
    : 30000
  return { command, commandArgs, db, cwd, timeoutMs }
}

function parseJsonOutput(stdout) {
  const text = String(stdout ?? '').trim()
  if (text === '') return { stdout: '' }
  for (const candidate of text.split(/\r?\n/).reverse()) {
    try {
      return JSON.parse(candidate)
    } catch {
      // Commands are permitted to write progress before their final JSON receipt.
    }
  }
  return { stdout: text }
}

async function invoke(config, args) {
  const resolved = commandConfig(config)
  const commandArgs = [...resolved.commandArgs]
  if (resolved.db) commandArgs.push('--db', resolved.db)
  commandArgs.push(...args)
  try {
    const result = await execFileAsync(resolved.command, commandArgs, {
      cwd: resolved.cwd,
      windowsHide: true,
      timeout: resolved.timeoutMs,
      maxBuffer: 1024 * 1024,
    })
    return {
      ok: true,
      exitCode: 0,
      resultJson: JSON.stringify(parseJsonOutput(result.stdout)),
      stderr: String(result.stderr ?? '').trim(),
    }
  } catch (error) {
    return {
      ok: false,
      exitCode: typeof error.code === 'number' ? error.code : null,
      resultJson: JSON.stringify(parseJsonOutput(error.stdout)),
      stderr: String(error.stderr ?? error.message ?? '').trim(),
    }
  }
}

const OUTPUT = {
  schema: {
    type: 'object',
    additionalProperties: false,
    properties: {
      ok: { type: 'boolean', required: true },
      exitCode: {
        oneOf: [{ type: 'number' }, { type: 'null' }],
        required: true,
      },
      resultJson: { type: 'string', required: true },
      stderr: { type: 'string', required: true },
    },
  },
  render: (_args, value) => [{
    type: 'text',
    text: JSON.stringify(value),
  }],
}

function present(title, args) {
  return { card: 'generic', title, kind: 'other', rawInput: args }
}

export function apply(ctx, config = {}) {
  ctx.tools.register(defineTool({
    name: 'allinflash_start',
    description: 'Create a durable All in Luna run from one concrete goal. This starts the coordinator but does not fabricate host execution receipts.',
    parameters: {
      goal: { type: 'string', required: true, description: 'Concrete completion goal for All in Luna.' },
    },
    output: OUTPUT,
    execute: (args) => invoke(config, ['start', '--goal', nonEmpty(args.goal, 'goal')]),
    presentCall: (args) => present('Start All in Luna run', args),
  }))
  ctx.tools.register(defineTool({
    name: 'allinflash_status',
    description: 'Read the durable status of an existing All in Luna run.',
    parameters: {
      run_id: { type: 'string', required: true, description: 'Run ID returned by allinflash_start.' },
    },
    output: OUTPUT,
    execute: (args) => invoke(config, ['status', nonEmpty(args.run_id, 'run_id')]),
    presentCall: (args) => present('Read All in Luna run status', args),
  }))
  ctx.tools.register(defineTool({
    name: 'allinflash_next_actions',
    description: 'Read exact pending All in Luna host actions. Relay a returned HostAction only through its named physical host capability.',
    parameters: {
      run_id: { type: 'string', required: true, description: 'Run ID returned by allinflash_start.' },
    },
    output: OUTPUT,
    execute: (args) => invoke(config, ['next-actions', nonEmpty(args.run_id, 'run_id')]),
    presentCall: (args) => present('Read All in Luna next actions', args),
  }))
}
