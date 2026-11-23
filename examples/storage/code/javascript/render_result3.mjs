import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";

const localRequire = createRequire(import.meta.url);
let api;
try {
  api = localRequire("@oai/artifact-tool");
} catch (_error) {
  const resolver = path.join(os.homedir(), ".cache", "codex-runtimes", "codex-primary-runtime", "dependencies", "node", "node_modules", "__resolver__.js");
  api = createRequire(resolver)("@oai/artifact-tool");
}

const { FileBlob, SpreadsheetFile } = api;
const input = process.argv[2];
const outputDir = process.argv[3];
await fs.mkdir(outputDir, { recursive: true });
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(input));
const jobs = [
  ["计划购电量", "A1:H5", "plan.png"],
  ["调整购电量", "A1:H5", "adjustment.png"],
  ["充放电量", "A1:F14", "storage.png"],
  ["紧急购电量", "A1:C20", "emergency.png"],
];
for (const [sheetName, range, name] of jobs) {
  const preview = await workbook.render({ sheetName, range, scale: 1, format: "png" });
  await fs.writeFile(path.join(outputDir, name), new Uint8Array(await preview.arrayBuffer()));
}
console.log(JSON.stringify({ status: "PASS", outputDir }));
