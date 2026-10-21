import fs from 'node:fs/promises'
import { existsSync } from 'node:fs'
import path from 'node:path'

export const name = 'google/zx'
export const repoUrl = 'https://github.com/google/zx.git'
export const repoDir = 'zx'

/**
 * Initializes the benchmark workspace:
 * Clones the repository if needed, changes directory into it, and captures baseline commit.
 */
export async function init({ rootDir }) {
  const targetDir = path.resolve(rootDir, repoDir)
  if (!existsSync(targetDir)) {
    echo(chalk.blue(`Cloning ${repoUrl}...`))
    await $`git clone ${repoUrl} ${targetDir}`
  }

  if (existsSync(targetDir)) {
    cd(targetDir)
  }

  const baseCommit = (await $`git rev-parse HEAD`).stdout.trim()
  return { baseCommit }
}

/**
 * Resets the repository to clean baseline before each run.
 */
export async function reset({ baseCommit }) {
  await $`git checkout -f ${baseCommit}`
  await $`git clean -fd`
}

export const scenarios = [
  {
    id: '1_read_env_audit',
    name: 'Environment Variable Audit (Read-Only)',
    prompt:
      'Inspect src/ and identify all environment variables accessed via process.env. Write the results into a file named env-audit.json in the current working directory with the following JSON format: {"variables": [{"name": string, "file": string, "defaultValue"?: string}]}. Do not edit or modify any existing source or test files.',
    validator: async () => {
      if (!existsSync('env-audit.json')) return false
      try {
        const content = await fs.readFile('env-audit.json', 'utf8')
        const data = JSON.parse(content)
        if (!Array.isArray(data.variables) || data.variables.length === 0) return false
        const varNames = data.variables.map((v) => v.name)
        // Verify key environment variables actually accessed via process.env in zx/src
        const expected = ['CI', 'ZX_REPL_HISTORY']
        const hasExpected = expected.every((v) => varNames.includes(v))

        // Ensure no source or test files were modified
        const gitDiff = await $`git diff --name-only`.nothrow()
        const noSourceEdits = gitDiff.stdout.trim() === ''
        return hasExpected && noSourceEdits
      } catch {
        return false
      }
    },
  },
  {
    id: '2_refactor_bool',
    name: 'Utility Modernization: parseBool (Small Refactor)',
    prompt:
      'In src/util.ts, enhance parseBool(v) to support numeric boolean representations: \'1\' and 1 should return true, while \'0\' and 0 should return false. Existing behaviors (\'true\' -> true, \'false\' -> false, non-empty string -> string) must remain intact. Add test assertions for these cases in test/util.test.js. Rebuild with \'npm run build:js\' and verify that \'node test/util.test.js\' passes.',
    validator: async () => {
      // 1. Build JS bundle
      const build = await $`npm run build:js`.nothrow()
      if (build.exitCode !== 0) return false

      // 2. Run targeted unit test
      const test = await $`node test/util.test.js`.nothrow()
      if (test.exitCode !== 0) return false

      // 3. Verify git diff is strictly confined to util files
      const gitDiff = await $`git diff --name-only`.nothrow()
      const changed = gitDiff.stdout.trim().split('\n').filter(Boolean)
      const validFiles = changed.every((f) => f.includes('util'))

      // 4. Assert parseBool behavior directly on build output
      const checkBehavior = await $`node -e '
        const { parseBool } = require("./build/util.cjs");
        if (parseBool(1) !== true || parseBool("1") !== true) process.exit(1);
        if (parseBool(0) !== false || parseBool("0") !== false) process.exit(1);
        if (parseBool("true") !== true || parseBool("false") !== false) process.exit(1);
        if (parseBool("custom") !== "custom") process.exit(1);
      '`.nothrow()

      return validFiles && checkBehavior.exitCode === 0
    },
  },
  {
    id: '3_feature_linear_backoff',
    name: 'Linear Backoff Strategy (Feature Addition)',
    prompt:
      'In src/goods.ts, implement and export a generator function: linearBackoff(max: Duration = \'60s\', delay: Duration = \'100ms\', step: Duration = \'100ms\'): Generator<number, void, unknown> that follows the generator pattern of expBackoff (yielding delay + step * n up to max). Export linearBackoff from src/goods.ts and src/index.ts. Add unit tests in test/goods.test.ts validating initial delay, step increment, max ceiling, and integration with retry(). Verify with: node --experimental-transform-types test/goods.test.ts',
    validator: async () => {
      // 1. Run goods unit test suite directly
      const test = await $`node --experimental-transform-types test/goods.test.ts`.nothrow()
      if (test.exitCode !== 0) return false

      // 2. Verify git diff includes linearBackoff export in src/
      const gitDiff = await $`git diff src/`.nothrow()
      if (!gitDiff.stdout.includes('linearBackoff')) return false

      // 3. Verify linearBackoff generator behavior directly
      const checkGen = await $`node --experimental-transform-types -e '
        import { linearBackoff } from "./src/goods.ts";
        const gen = linearBackoff("500ms", "100ms", "100ms");
        const v = [gen.next().value, gen.next().value, gen.next().value, gen.next().value, gen.next().value, gen.next().value];
        if (v[0] !== 100 || v[1] !== 200 || v[2] !== 300 || v[3] !== 400 || v[4] !== 500 || v[5] !== 500) {
          process.exit(1);
        }
      '`.nothrow()

      return checkGen.exitCode === 0
    },
  },
  {
    id: '4_feature_cli_dry_run',
    name: 'CLI Dry Run Mode (Feature Addition)',
    prompt:
      'In src/cli.ts, add support for a --dry-run flag. When --dry-run is supplied (e.g. zx --dry-run script.mjs or zx --dry-run -e \'...\'), zx must print [dry-run] followed by the command or script path/code that would be executed, and exit with code 0 without actually executing the script or child processes. Compile with \'npm run build:js\' and verify using \'node build/cli.js --dry-run -e "echo 123"\'.',
    validator: async () => {
      // 1. Compile CLI
      const build = await $`npm run build:js`.nothrow()
      if (build.exitCode !== 0) return false

      // 2. Test dry-run with inline eval
      const dryRun = await $`node build/cli.js --dry-run -e 'echo "dry run test"'`.nothrow()
      if (dryRun.exitCode !== 0 || !dryRun.stdout.includes('[dry-run]')) return false

      // 3. Ensure dry-run did not execute side effects
      const testMarker = '/tmp/zx-benchmark-dry-run-check'
      await $`rm -f ${testMarker}`.nothrow()
      await $`node build/cli.js --dry-run -e 'fs.writeFileSync("${testMarker}", "bad")'`.nothrow()
      if (existsSync(testMarker)) {
        await $`rm -f ${testMarker}`.nothrow()
        return false
      }

      return true
    },
  },
]

export default {
  name,
  repoUrl,
  repoDir,
  init,
  reset,
  scenarios,
}
