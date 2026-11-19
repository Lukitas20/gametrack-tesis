// Valida explícitamente como módulos, igual que <script type="module">.
// La detección automática de Node para .js puede omitir errores de parseo.
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

function files(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const path = join(directory, entry.name);
    return entry.isDirectory() ? files(path) : path.endsWith(".js") ? [path] : [];
  });
}

const modules = files(fileURLToPath(new URL("../js/", import.meta.url)));
let failed = false;
for (const path of modules) {
  const result = spawnSync(process.execPath, ["--input-type=module", "--check"], {
    input: readFileSync(path), encoding: "utf8",
  });
  if (result.status !== 0) {
    failed = true;
    console.error(path, result.error?.message || result.stderr);
  }
}
console.log(`${modules.length} módulos revisados: ${failed ? "hay errores" : "OK"}`);
process.exitCode = failed ? 1 : 0;
