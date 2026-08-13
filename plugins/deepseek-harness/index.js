/**
 * All in Luna's model-facing DeepSeek Harness integration.
 *
 * The plugin intentionally owns only the DSH tool surface.  It invokes the
 * public All in Luna CLI and returns its JSON receipt unchanged, so workflow
 * semantics, durable state, and host-action authority remain in Python.
 */
import { execFile } from 'node:child_process'
import { createHash } from 'node:crypto'
import { promisify } from 'node:util'
import { defineTool } from '@deepseek-ai/dsh-tools'

const execFileAsync = promisify(execFile)

export const name = 'allinflash'
export const inject = ['tools', 'subagents']

const CREATE_TOP_LEVEL_TASK_TOOL = 'allinflash__create_top_level_task'

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

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`
  if (value !== null && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(',')}}`
  }
  return JSON.stringify(value)
}

function actionContractHash(action) {
  const material = {
    kind: action.kind,
    tool: action.tool,
    arguments: action.arguments,
    task_id: action.task_id ?? null,
    dispatch_id: action.dispatch_id ?? null,
    execution_class: action.execution_class,
    task_envelope_ref: action.task_envelope_ref ?? null,
  }
  return createHash('sha256').update(canonicalJson(material)).digest('hex')
}

function parseRelayAction(actionJson) {
  let action
  try {
    action = JSON.parse(nonEmpty(actionJson, 'action_json'))
  } catch (error) {
    throw new Error(`action_json must be valid JSON: ${error.message}`)
  }
  if (action?.kind !== 'create-top-level-task' || action.execution_class !== 'top_level_task') {
    throw new Error('All in Flash relays only top-level task actions')
  }
  if (action.tool !== CREATE_TOP_LEVEL_TASK_TOOL || action.host_capability_required !== CREATE_TOP_LEVEL_TASK_TOOL) {
    throw new Error('action does not require the exact All in Flash top-level capability')
  }
  if (action.tool_policy?.exact_tool !== CREATE_TOP_LEVEL_TASK_TOOL || (action.tool_policy?.substitutions ?? []).length !== 0) {
    throw new Error('action tool policy is not an exact All in Flash opcode policy')
  }
  if (action.action_contract_hash !== actionContractHash(action)) {
    throw new Error('action_contract_hash does not match immutable action material')
  }
  if (typeof action.arguments?.prompt !== 'string' || typeof action.arguments?.title !== 'string') {
    throw new Error('top-level action is missing its frozen prompt or title')
  }
  return action
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

function startArguments(config, goal, model) {
  const resolvedModel = model ?? config.model
  const args = ['start', '--goal', nonEmpty(goal, 'goal')]
  if (typeof resolvedModel === 'string' && resolvedModel.trim()) args.push('--model', resolvedModel.trim())
  return args
}

export function apply(ctx, config = {}) {
  ctx.tools.register(defineTool({
    name: 'allinflash_start',
    description: 'Create a durable All in Luna run from one concrete goal. This starts the coordinator but does not fabricate host execution receipts.',
    parameters: {
      goal: { type: 'string', required: true, description: 'Concrete completion goal for All in Luna.' },
      model: { type: 'string', description: 'Optional explicit model route; defaults to the profile model.' },
    },
    output: OUTPUT,
    execute: (args) => invoke(config, startArguments(config, args.goal, args.model)),
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
  ctx.tools.register(defineTool({
    name: 'allinflash_relay_action',
    description: 'Relay one frozen All in Flash top-level HostAction into a real DSH continuable child, then persist its observed child identity as the action receipt. The action JSON must be returned unchanged by allinflash_next_actions.',
    parameters: {
      action_json: { type: 'string', required: true, description: 'Exact JSON object returned by allinflash_next_actions.' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          childId: { type: 'string', required: true },
          receiptJson: { type: 'string', required: true },
        },
      },
      render: (_args, value) => [{ type: 'text', text: `All in Flash lane child ${value.childId} accepted.` }],
    },
    async execute(args, exec) {
      const action = parseRelayAction(args.action_json)
      if (!exec.agent) throw new Error('allinflash_relay_action requires a calling DSH agent')
      const provider = typeof config.subagentProvider === 'string' && config.subagentProvider.trim()
        ? config.subagentProvider.trim()
        : 'spawn'
      const accepted = await ctx.subagents.startContinuable({
        provider,
        label: action.arguments.title,
        request: {
          label: action.arguments.title,
          prompt: [{ type: 'text', text: action.arguments.prompt }],
          parent: exec.agent,
        },
        signal: exec.signal,
      })
      const runId = String(action.payload?.task_envelope?.run_ref ?? '').replace('run://', '')
      if (!runId) throw new Error('action payload does not contain a durable run reference')
      const receipt = {
        receipt_id: `allinflash-receipt-${action.action_id}`,
        status: 'ready',
        source: 'deepseek-harness',
        host_adapter: 'deepseek-harness',
        host_id: typeof config.hostId === 'string' && config.hostId.trim() ? config.hostId.trim() : 'allinflash-dsh',
        thread_id: accepted.childId,
        action_id: action.action_id,
        action_kind: action.kind,
        idempotency_key: action.idempotency_key,
        dispatch_key: action.idempotency_key,
        dispatch_id: action.dispatch_id,
        task_id: action.task_id,
        actual: true,
        actual_tool: CREATE_TOP_LEVEL_TASK_TOOL,
        actual_capability: CREATE_TOP_LEVEL_TASK_TOOL,
        action_contract_hash: action.action_contract_hash,
        payload: { provider, child_id: accepted.childId },
      }
      const ingested = await invoke(config, ['ingest-receipt', runId, JSON.stringify(receipt)])
      if (!ingested.ok) throw new Error(`failed to ingest All in Flash receipt: ${ingested.stderr || ingested.resultJson}`)
      return { childId: accepted.childId, receiptJson: JSON.stringify(receipt) }
    },
    presentCall: (args) => present('Relay All in Flash task', args),
  }))
}
