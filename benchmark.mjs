#!/usr/bin/env zx

import fs from 'node:fs/promises'
import { existsSync, createReadStream } from 'node:fs'
import readline from 'node:readline'
import path from 'node:path'
import os from 'node:os'
import { pathToFileURL } from 'node:url'

$.verbose = false // Keeps console output clean during iterations

const ROOT_DIR = process.cwd()

// Helper to parse comma-separated or repeated CLI arguments
const parseList = (arg) => {
  if (!arg) return []
  if (Array.isArray(arg)) return arg.map(String).flatMap(s => s.split(',')).map(s => s.trim()).filter(Boolean)
  return String(arg).split(',').map(s => s.trim()).filter(Boolean)
}

// Config file resolution (defaults to zx.config.mjs)
const configArg = argv.config ?? argv.c
const DEFAULT_CONFIG = 'zx.config.mjs'
const configPath = path.resolve(ROOT_DIR, configArg || DEFAULT_CONFIG)

let config = null
if (existsSync(configPath)) {
  try {
    const imported = await import(pathToFileURL(configPath).href)
    config = imported.default || imported
  } catch (err) {
    echo(chalk.red(`Error loading config file (${configPath}): ${err.message}`))
    console.error(err)
    process.exit(1)
  }
} else if (configArg) {
  echo(chalk.red(`Error: Config file not found: ${configArg}`))
  process.exit(1)
}

const allTestCases = config?.scenarios || config?.testCases || []

// Display usage / help
if (argv.help || argv.h) {
  echo(chalk.bold('Usage: npx zx benchmark.mjs [options]'))
  echo('\nOptions:')
  echo('  --config, -c                 Config file path [default: zx.config.mjs]')
  echo('  --models, --model, -m        Model names (e.g. gemini-3.8-flash-low,gemini-4-argon-high) [default: gemini-3.8-flash-{low,medium,high},gemini-4-argon-{low,medium,high}]')
  echo('  --repetitions, -r            Number of repetitions per scenario [default: 3]')
  echo('  --scenarios, --scenario, -s  Scenario IDs to run (e.g. 1_audit,2_refactor) [default: all]')
  echo(`\nAvailable scenarios (${configArg || DEFAULT_CONFIG}):`)
  if (allTestCases.length > 0) {
    for (const t of allTestCases) {
      echo(`  - ${chalk.bold(t.id)}: ${t.name}`)
    }
  } else {
    echo(chalk.yellow('  (No scenarios found in config)'))
  }
  process.exit(0)
}

if (!config) {
  echo(chalk.red(`Error: Default config file '${DEFAULT_CONFIG}' not found and no --config specified.`))
  process.exit(1)
}

if (allTestCases.length === 0) {
  echo(chalk.red(`Error: No scenarios defined in config file: ${configPath}`))
  process.exit(1)
}

// Wait for a file to appear on disk
async function waitForFile(filePath, timeoutMs = 2000) {
  const start = Date.now()
  while (Date.now() - start < timeoutMs) {
    if (existsSync(filePath)) return true
    await sleep(100)
  }
  return existsSync(filePath)
}

// Extract conversation ID from the agy debug log file
async function extractConversationId(logFilePath) {
  if (!existsSync(logFilePath)) return null
  const rl = readline.createInterface({
    input: createReadStream(logFilePath),
    crlfDelay: Infinity,
  })
  try {
    for await (const line of rl) {
      const match = line.match(/Created conversation ([a-f0-9\-]+)/)
      if (match) {
        rl.close()
        return match[1]
      }
    }
  } catch {
    // Ignore read errors
  }
  return null
}

// Stream and aggregate metrics from transcript_full.jsonl without loading full file into memory
async function parseTranscript(transcriptPath) {
  const defaultMetrics = {
    inputTokens: 0,
    outputTokens: 0,
    cacheReadTokens: 0,
    totalTokens: 0,
    toolInvocations: 0,
    toolCallsByName: {},
    numTurns: 0,
  }

  if (!existsSync(transcriptPath)) {
    return defaultMetrics
  }

  const fileStream = createReadStream(transcriptPath)
  const rl = readline.createInterface({
    input: fileStream,
    crlfDelay: Infinity,
  })

  let inputTokens = 0
  let outputTokens = 0
  let cacheReadTokens = 0
  let toolInvocations = 0
  let numTurns = 0
  const toolCallsByName = {}

  try {
    for await (const line of rl) {
      if (!line.trim()) continue
      try {
        const entry = JSON.parse(line)
        if (entry.type === 'PLANNER_RESPONSE') {
          numTurns++
          if (typeof entry.input_tokens === 'number') inputTokens += entry.input_tokens
          if (typeof entry.output_tokens === 'number') outputTokens += entry.output_tokens
          if (typeof entry.cache_read_tokens === 'number') cacheReadTokens += entry.cache_read_tokens
          if (Array.isArray(entry.tool_calls)) {
            toolInvocations += entry.tool_calls.length
            for (const tc of entry.tool_calls) {
              const name = tc.name || 'unknown'
              toolCallsByName[name] = (toolCallsByName[name] || 0) + 1
            }
          }
        }
      } catch {
        // Skip individual unparseable lines if any
      }
    }
  } catch {
    // Return accumulated metrics if stream is aborted
  }

  return {
    inputTokens,
    outputTokens,
    cacheReadTokens,
    totalTokens: inputTokens + outputTokens + cacheReadTokens,
    toolInvocations,
    toolCallsByName,
    numTurns,
  }
}

// Configurable benchmark dimensions
const parsedModels = parseList(argv.models ?? argv.model ?? argv.m ?? argv.efforts ?? argv.effort ?? argv.e)
const MODELS = parsedModels.length > 0
  ? parsedModels
  : [
      'gemini-3.8-flash-low',
      'gemini-3.8-flash-medium',
      'gemini-3.8-flash-high',
      'gemini-4-argon-low',
      'gemini-4-argon-medium',
      'gemini-4-argon-high',
    ]

const rawReps = argv.repetitions ?? argv.repetition ?? argv.r
const parsedReps = Number(rawReps)
const REPETITIONS = !isNaN(parsedReps) && parsedReps > 0 ? parsedReps : 3

const parsedScenarios = parseList(argv.scenarios ?? argv.scenario ?? argv.s)
const testCases = parsedScenarios.length > 0
  ? allTestCases.filter(t => parsedScenarios.includes(t.id) || parsedScenarios.some(s => t.id.toLowerCase().includes(s.toLowerCase()) || t.name.toLowerCase().includes(s.toLowerCase())))
  : allTestCases

if (testCases.length === 0) {
  echo(chalk.red(`No scenarios matched: ${parsedScenarios.join(', ')}`))
  echo(chalk.yellow(`Available scenarios: ${allTestCases.map(t => t.id).join(', ')}`))
  process.exit(1)
}

// Pricing configuration
const PRICING_FILE = path.resolve(ROOT_DIR, 'api_list_prices.json')
let pricingData = null
if (existsSync(PRICING_FILE)) {
  try {
    pricingData = JSON.parse(await fs.readFile(PRICING_FILE, 'utf8'))
  } catch (err) {
    echo(chalk.yellow(`Warning: Failed to load pricing file ${PRICING_FILE}: ${err.message}`))
  }
}

function calculateCost(modelName, inputTokens, cacheReadTokens, outputTokens) {
  if (!pricingData?.models) return null
  let p = pricingData.models[modelName]
  if (!p) {
    const baseModel = modelName.replace(/-(low|medium|high|xhigh|max)$/, '')
    p = pricingData.models[baseModel]
  }
  if (!p) {
    const matchedKey = Object.keys(pricingData.models).find(key => modelName.startsWith(key))
    if (matchedKey) p = pricingData.models[matchedKey]
  }
  if (!p) return null

  const inputCost = (inputTokens / 1_000_000) * (p.input || 0)
  const cachedCost = (cacheReadTokens / 1_000_000) * (p.cached_input || 0)
  const outputCost = (outputTokens / 1_000_000) * (p.output || 0)
  return parseFloat((inputCost + cachedCost + outputCost).toFixed(6))
}

function extractEffort(modelName) {
  const match = modelName.match(/-(low|medium|high|xhigh|max)$/)
  return match ? match[1] : null
}

// Setup benchmark-runs directory: benchmark-runs/YYYYMMDDHHMM
const formatTimestamp = (d = new Date()) => {
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}${pad(d.getHours())}${pad(d.getMinutes())}`
}

const timestamp = formatTimestamp()
let RUN_DIR = path.resolve(ROOT_DIR, 'benchmark-runs', timestamp)
if (existsSync(RUN_DIR)) {
  const pad = (n) => String(n).padStart(2, '0')
  RUN_DIR = path.resolve(ROOT_DIR, 'benchmark-runs', `${timestamp}_${pad(new Date().getSeconds())}`)
}
await fs.mkdir(RUN_DIR, { recursive: true })
const RESULTS_FILE = path.join(RUN_DIR, 'results.jsonl')

// Initialize benchmark target workspace
let baseCommit = ''
if (typeof config.init === 'function') {
  const initResult = await config.init({ rootDir: ROOT_DIR, runDir: RUN_DIR })
  baseCommit = initResult?.baseCommit ?? ''
} else {
  const targetDir = config.repoDir ? path.resolve(ROOT_DIR, config.repoDir) : ROOT_DIR
  if (config.repoUrl && !existsSync(targetDir) && !existsSync(path.join(ROOT_DIR, '.git'))) {
    echo(chalk.blue(`Cloning ${config.repoUrl}...`))
    await $`git clone ${config.repoUrl} ${targetDir}`
  }
  if (existsSync(targetDir)) {
    cd(targetDir)
  }
  baseCommit = (await $`git rev-parse HEAD`).stdout.trim()
}

const benchmarkName = config.name || config.repoDir || 'benchmark'
echo(chalk.bold.cyan(`\nStarting agy benchmark on ${benchmarkName} (Baseline: ${baseCommit})`))
echo(chalk.dim(`Artifacts: ${RUN_DIR}`))
echo(chalk.dim(`Config: file=${path.relative(ROOT_DIR, configPath)}, models=[${MODELS.join(', ')}], repetitions=${REPETITIONS}, scenarios=[${testCases.map(t => t.id).join(', ')}]\n`))

for (const test of testCases) {
  echo(chalk.bold.yellow(`\n=== Scenario: ${test.name} ===`))

  for (const model of MODELS) {
    for (let run = 1; run <= REPETITIONS; run++) {
      process.stdout.write(
        chalk.blue(`[Model: ${model.padEnd(24)}] Run ${run}/${REPETITIONS} ... `)
      )

      // 1. Reset repo to pure baseline
      if (typeof config.reset === 'function') {
        await config.reset({ baseCommit, runDir: RUN_DIR, test, model, run })
      } else if (baseCommit) {
        await $`git checkout -f ${baseCommit}`
        await $`git clean -fd`
      }

      // 2. Measure execution time and invoke agy
      const startTime = performance.now()
      const safeModel = model.replace(/[^a-zA-Z0-9._-]/g, '_')
      const logFileName = `agy_${test.id}_${safeModel}_run${run}.log`
      const logFile = path.join(RUN_DIR, logFileName)
      const transcriptFileName = `transcript_${test.id}_${safeModel}_run${run}.jsonl`
      const transcriptDestPath = path.join(RUN_DIR, transcriptFileName)
      
      const agyProc = await $`agy --model ${model} \
        --mode accept-edits \
        --dangerously-skip-permissions \
        --sandbox \
        --log-file=${logFile} \
        -p ${test.prompt}`.nothrow()

      const durationSec = ((performance.now() - startTime) / 1000).toFixed(2)

      // 3. Extract metrics from agy debug log & full transcript
      await waitForFile(logFile, 2000)
      const conversationId = await extractConversationId(logFile)

      let metrics = {
        inputTokens: 0,
        outputTokens: 0,
        cacheReadTokens: 0,
        totalTokens: 0,
        toolInvocations: 0,
        toolCallsByName: {},
        numTurns: 0,
      }

      if (conversationId) {
        const transcriptPath = path.join(
          os.homedir(),
          '.gemini/antigravity-cli/brain',
          conversationId,
          '.system_generated/logs/transcript_full.jsonl'
        )
        const hasTranscript = await waitForFile(transcriptPath, 3000)
        if (hasTranscript) {
          try {
            await fs.copyFile(transcriptPath, transcriptDestPath)
            metrics = await parseTranscript(transcriptDestPath)
          } catch {
            metrics = await parseTranscript(transcriptPath)
          }
        }
      }

      const rawLog = agyProc.stdout + '\n' + agyProc.stderr
      const passed = await test.validator(rawLog)
      const costUsd = calculateCost(model, metrics.inputTokens, metrics.cacheReadTokens, metrics.outputTokens)
      const costStr = costUsd !== null ? ` cost: $${costUsd.toFixed(4)}` : ''

      const outcomeColor = passed ? chalk.green('PASSED') : chalk.red('FAILED')
      echo(
        `${outcomeColor} in ${durationSec}s ` +
        `(${metrics.toolInvocations} tool calls, ` +
        `tokens: in=${metrics.inputTokens} out=${metrics.outputTokens} cache=${metrics.cacheReadTokens}${costStr})`
      )

      // 4. Record JSONL sample
      const record = {
        timestamp: new Date().toISOString(),
        config: path.basename(configPath),
        case_id: test.id,
        model,
        effort: extractEffort(model),
        run,
        duration_sec: parseFloat(durationSec),
        passed,
        exit_code: agyProc.exitCode,
        conversation_id: conversationId,
        input_tokens: metrics.inputTokens,
        output_tokens: metrics.outputTokens,
        cache_read_tokens: metrics.cacheReadTokens,
        total_tokens: metrics.totalTokens,
        cost_usd: costUsd,
        tool_invocations: metrics.toolInvocations,
        tool_calls: metrics.toolCallsByName,
        num_turns: metrics.numTurns,
        log_file: logFileName,
        transcript_file: existsSync(transcriptDestPath) ? transcriptFileName : null,
      }

      await fs.appendFile(RESULTS_FILE, JSON.stringify(record) + '\n')
    }
  }
}

echo(chalk.bold.green(`\nBenchmark run complete! Results and artifacts logged to ${RUN_DIR}`))