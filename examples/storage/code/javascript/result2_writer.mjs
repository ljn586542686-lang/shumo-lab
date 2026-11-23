import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";


const EXPECTED_SHEETS = ["计划购电量", "充放电量", "紧急购电量"];
const POSITIONS_PER_DAY = 144;
const BLOCKS_PER_DAY = 6;
const POSITIONS_PER_BLOCK = 24;
const EMERGENCY_TOLERANCE = 1e-7;


function loadArtifactTool() {
  const localRequire = createRequire(import.meta.url);
  try {
    return localRequire("@oai/artifact-tool");
  } catch (_error) {
    const runtimeResolver = path.join(
      os.homedir(),
      ".cache",
      "codex-runtimes",
      "codex-primary-runtime",
      "dependencies",
      "node",
      "node_modules",
      "__resolver__.js",
    );
    return createRequire(runtimeResolver)("@oai/artifact-tool");
  }
}


function parseArgs(argv) {
  const values = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith("--") || value === undefined) {
      throw new Error("RESULT2_WRITER_ARGUMENT_INVALID");
    }
    values[key.slice(2)] = value;
  }
  for (const required of ["template", "ledger", "output"]) {
    if (!values[required]) {
      throw new Error(`RESULT2_WRITER_ARGUMENT_MISSING:${required}`);
    }
  }
  return values;
}


function assertLedger(ledger) {
  if (ledger.status !== "PASS" || ledger.scope?.days !== 334) {
    throw new Error("RESULT2_LEDGER_NOT_FORMALLY_ACCEPTED");
  }
  if (!Array.isArray(ledger.daily_results) || ledger.daily_results.length !== 334) {
    throw new Error("RESULT2_LEDGER_DAY_COUNT_MISMATCH");
  }
  for (const day of ledger.daily_results) {
    for (const field of ["Q", "G", "H", "c", "d"]) {
      if (!Array.isArray(day.trajectories?.[field]) || day.trajectories[field].length !== POSITIONS_PER_DAY) {
        throw new Error(`RESULT2_LEDGER_POSITION_COUNT_MISMATCH:${day.day}:${field}`);
      }
    }
    if (!Array.isArray(day.trajectories?.E) || day.trajectories.E.length !== POSITIONS_PER_DAY + 1) {
      throw new Error(`RESULT2_LEDGER_SOC_PATH_COUNT_MISMATCH:${day.day}`);
    }
  }
}


function excelDate(day) {
  return new Date(`${day}T00:00:00Z`);
}


async function main() {
  const args = parseArgs(process.argv.slice(2));
  const ledger = JSON.parse(await fs.readFile(args.ledger, "utf8"));
  assertLedger(ledger);
  const { FileBlob, SpreadsheetFile } = loadArtifactTool();
  const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(args.template));
  const names = workbook.worksheets.items.map((sheet) => sheet.name);
  if (JSON.stringify(names) !== JSON.stringify(EXPECTED_SHEETS)) {
    throw new Error("RESULT2_TEMPLATE_SHEET_MISMATCH");
  }

  const plan = workbook.worksheets.getItem("计划购电量");
  const planValues = ledger.daily_results.map((day) => day.trajectories.Q.map(Number));
  const planTotals = ledger.daily_results.map((day) => [Number(day.totals.planned_purchase_kwh)]);
  const planCosts = ledger.daily_results.map((day) => [Number(day.costs.planned_purchase_cost)]);
  plan.getRangeByIndexes(1, 1, 334, POSITIONS_PER_DAY).values = planValues;
  plan.getRangeByIndexes(1, 145, 334, 1).values = planTotals;
  plan.getRangeByIndexes(1, 146, 334, 1).values = planCosts;

  const storage = workbook.worksheets.getItem("充放电量");
  const sourceStyle = storage.getRange("A2:F7");
  storage.getRangeByIndexes(1, 0, 334 * BLOCKS_PER_DAY, 6).copyFrom(sourceStyle, "all");
  const storageRows = [];
  const blockLabels = ["0:00-4:00", "4:00-8:00", "8:00-12:00", "12:00-16:00", "16:00-20:00", "20:00-24:00"];
  for (const day of ledger.daily_results) {
    for (let block = 0; block < BLOCKS_PER_DAY; block += 1) {
      const start = block * POSITIONS_PER_BLOCK;
      const end = start + POSITIONS_PER_BLOCK;
      const charge = day.trajectories.c.slice(start, end).reduce((total, value) => total + Number(value), 0);
      const discharge = day.trajectories.d.slice(start, end).reduce((total, value) => total + Number(value), 0);
      storageRows.push([
        block === 0 ? excelDate(day.day) : null,
        blockLabels[block],
        charge,
        discharge,
        block === 0 ? "0:00" : block === 1 ? "24:00" : null,
        block === 0 ? Number(day.trajectories.E[0]) : block === 1 ? Number(day.trajectories.E.at(-1)) : null,
      ]);
    }
  }
  storage.getRangeByIndexes(1, 0, storageRows.length, 6).values = storageRows;

  const emergency = workbook.worksheets.getItem("紧急购电量");
  const emergencySourceStyle = emergency.getRange("A2:C2");
  const positionLabels = plan.getRangeByIndexes(0, 1, 1, POSITIONS_PER_DAY).values[0];
  const emergencyRows = [];
  for (const day of ledger.daily_results) {
    let firstForDay = true;
    for (let position = 0; position < POSITIONS_PER_DAY; position += 1) {
      const amount = Number(day.trajectories.H[position]);
      if (amount > EMERGENCY_TOLERANCE) {
        emergencyRows.push([
          firstForDay ? excelDate(day.day) : null,
          positionLabels[position],
          amount,
        ]);
        firstForDay = false;
      }
    }
  }
  const clearRows = Math.max(10, emergencyRows.length);
  emergency.getRangeByIndexes(1, 0, clearRows, 3).clear({ applyTo: "contents" });
  emergency.getRangeByIndexes(1, 0, clearRows, 3).copyFrom(emergencySourceStyle, "all");
  if (emergencyRows.length > 0) {
    emergency.getRangeByIndexes(1, 0, emergencyRows.length, 3).values = emergencyRows;
  }

  workbook.recalculate();
  await fs.mkdir(path.dirname(args.output), { recursive: true });
  const exported = await SpreadsheetFile.exportXlsx(workbook);
  await exported.save(args.output);
  console.log(JSON.stringify({ status: "PASS", output: args.output, emergency_rows: emergencyRows.length }));
}


main().catch((error) => {
  console.error(JSON.stringify({ status: "FAIL", reason: String(error?.message ?? error) }));
  process.exit(1);
});
