import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";

const DAYS = 334;
const POSITIONS = 144;
const BLOCKS = 6;
const POSITIONS_PER_BLOCK = 24;
const EMERGENCY_ZERO_THRESHOLD = 1e-7;
const TEMPLATE_SHA256 = (process.env.Q3_TEMPLATE_SHA256 || "").toLowerCase();
const EXPECTED_SHEETS = ["计划购电量", "调整购电量", "充放电量", "紧急购电量"];
const BLOCK_LABELS = ["0:00-4:00", "4:00-8:00", "8:00-12:00", "12:00-16:00", "16:00-20:00", "20:00-24:00"];

function loadArtifactTool() {
  const localRequire = createRequire(import.meta.url);
  try {
    return localRequire("@oai/artifact-tool");
  } catch (_error) {
    const resolver = path.join(os.homedir(), ".cache", "codex-runtimes", "codex-primary-runtime", "dependencies", "node", "node_modules", "__resolver__.js");
    return createRequire(resolver)("@oai/artifact-tool");
  }
}

function parseArgs(argv) {
  const result = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith("--") || value === undefined) throw new Error("Q3_RESULT3_WRITER_ARGUMENT_INVALID");
    result[key.slice(2)] = value;
  }
  for (const required of ["template", "ledger", "output"]) {
    if (!result[required]) throw new Error(`Q3_RESULT3_WRITER_ARGUMENT_MISSING:${required}`);
  }
  return result;
}

async function sha256(file) {
  const crypto = await import("node:crypto");
  return crypto.createHash("sha256").update(await fs.readFile(file)).digest("hex");
}

function assertLedger(ledger) {
  if (ledger?.status !== "PASS" || ledger?.scope?.days !== DAYS || !Array.isArray(ledger?.daily_results) || ledger.daily_results.length !== DAYS) {
    throw new Error("Q3_RESULT3_LEDGER_NOT_COMPLETE");
  }
  const dates = new Set();
  for (const day of ledger.daily_results) {
    if (typeof day.date !== "string" || dates.has(day.date) || day.day_scientific_validation?.status !== "PASS") {
      throw new Error("Q3_RESULT3_DAILY_EVIDENCE_INVALID");
    }
    dates.add(day.date);
    for (const field of ["plan_q0", "final_effective_plan", "charge", "discharge", "emergency"]) {
      if (!Array.isArray(day[field]) || day[field].length !== POSITIONS) throw new Error(`Q3_RESULT3_VECTOR_INVALID:${day.date}:${field}`);
    }
    if (!Array.isArray(day.soc_path) || day.soc_path.length !== POSITIONS + 1) throw new Error(`Q3_RESULT3_SOC_INVALID:${day.date}`);
  }
}

function excelDate(day) {
  return new Date(`${day}T00:00:00Z`);
}

function sum(values) {
  return values.reduce((total, value) => total + Number(value), 0);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if ((await sha256(args.template)).toLowerCase() !== TEMPLATE_SHA256) throw new Error("Q3_RESULT3_TEMPLATE_SHA256_MISMATCH");
  const ledger = JSON.parse(await fs.readFile(args.ledger, "utf8"));
  assertLedger(ledger);
  const { FileBlob, SpreadsheetFile } = loadArtifactTool();
  const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(args.template));
  if (JSON.stringify(workbook.worksheets.items.map((sheet) => sheet.name)) !== JSON.stringify(EXPECTED_SHEETS)) {
    throw new Error("Q3_RESULT3_TEMPLATE_SHEET_MISMATCH");
  }

  const writePlanSheet = (name, vectorField, costForDay) => {
    const sheet = workbook.worksheets.getItem(name);
    sheet.getRangeByIndexes(1, 1, DAYS, POSITIONS).values = ledger.daily_results.map((day) => day[vectorField].map(Number));
    sheet.getRangeByIndexes(1, 145, DAYS, 1).values = ledger.daily_results.map((day) => [sum(day[vectorField])]);
    sheet.getRangeByIndexes(1, 146, DAYS, 1).values = ledger.daily_results.map((day) => [costForDay(day)]);
  };
  writePlanSheet("计划购电量", "plan_q0", (day) => Number(day.initial_plan_cost));
  writePlanSheet("调整购电量", "final_effective_plan", (day) => Number(day.initial_plan_cost) + Number(day.adjustment_cost));

  const storage = workbook.worksheets.getItem("充放电量");
  const storageStyleSource = storage.getRange("A2:F7");
  for (let dayIndex = 0; dayIndex < DAYS; dayIndex += 1) {
    storage.getRangeByIndexes(1 + dayIndex * BLOCKS, 0, BLOCKS, 6).copyFrom(storageStyleSource, "all");
  }
  const storageRows = [];
  for (const day of ledger.daily_results) {
    for (let block = 0; block < BLOCKS; block += 1) {
      const start = block * POSITIONS_PER_BLOCK;
      const end = start + POSITIONS_PER_BLOCK;
      storageRows.push([
        block === 0 ? excelDate(day.date) : null,
        BLOCK_LABELS[block],
        sum(day.charge.slice(start, end)),
        sum(day.discharge.slice(start, end)),
        block === 0 ? new Date("1899-12-30T00:00:00Z") : block === 1 ? "24:00" : null,
        block === 0 ? Number(day.soc_path[0]) : block === 1 ? Number(day.soc_path.at(-1)) : null,
      ]);
    }
  }
  storage.getRangeByIndexes(1, 0, storageRows.length, 6).values = storageRows;
  // Artifact-tool imports retain the template's values but do not reliably
  // propagate number formats into all dynamically extended rows.  Restore the
  // three official display formats without changing the underlying values.
  for (let dayIndex = 0; dayIndex < DAYS; dayIndex += 1) {
    const firstRow = 1 + dayIndex * BLOCKS;
    storage.getRangeByIndexes(firstRow, 0, 1, 1).format.numberFormat = "mm-dd-yy";
    storage.getRangeByIndexes(firstRow, 4, 1, 1).format.numberFormat = "h:mm";
    storage.getRangeByIndexes(firstRow + 1, 4, 1, 1).format.numberFormat = "@";
  }

  const emergency = workbook.worksheets.getItem("紧急购电量");
  const labels = workbook.worksheets.getItem("计划购电量").getRangeByIndexes(0, 1, 1, POSITIONS).values[0];
  const emergencyRows = [];
  for (const day of ledger.daily_results) {
    let firstForDay = true;
    day.emergency.forEach((value, position) => {
      if (Number(value) > EMERGENCY_ZERO_THRESHOLD) {
        emergencyRows.push([firstForDay ? excelDate(day.date) : null, labels[position], Number(value)]);
        firstForDay = false;
      }
    });
  }
  const clearRows = Math.max(10, emergencyRows.length);
  emergency.getRangeByIndexes(1, 0, clearRows, 3).clear({ applyTo: "contents" });
  const emergencyStyleSource = emergency.getRange("A2:C2");
  for (let row = 0; row < emergencyRows.length; row += 1) {
    emergency.getRangeByIndexes(1 + row, 0, 1, 3).copyFrom(emergencyStyleSource, "all");
  }
  emergency.getRangeByIndexes(1, 0, emergencyRows.length, 3).values = emergencyRows;
  for (let row = 0; row < emergencyRows.length; row += 1) {
    if (emergencyRows[row][0] !== null) {
      emergency.getRangeByIndexes(1 + row, 0, 1, 1).format.numberFormat = "mm-dd-yy";
    }
  }

  workbook.recalculate();
  await fs.mkdir(path.dirname(args.output), { recursive: true });
  const exported = await SpreadsheetFile.exportXlsx(workbook);
  await exported.save(args.output);
  console.log(JSON.stringify({ status: "PASS", emergency_rows: emergencyRows.length, output: args.output }));
}

main().catch((error) => {
  console.error(JSON.stringify({ status: "FAIL", reason: String(error?.message ?? error) }));
  process.exit(1);
});
