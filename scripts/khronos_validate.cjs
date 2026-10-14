#!/usr/bin/env node
// Validate GLB files with the official Khronos glTF-Validator (independent of BAYMAX's own validator).
// Usage: node scripts/khronos_validate.cjs <file-or-dir> [...]   (run `npm install` in desktop/ first)
const fs = require("fs");
const path = require("path");
const validator = require(path.join(__dirname, "..", "desktop", "node_modules", "gltf-validator"));

(async () => {
  const files = process.argv.slice(2).flatMap((p) => (fs.statSync(p).isDirectory()
    ? fs.readdirSync(p).filter((f) => f.endsWith(".glb")).map((f) => path.join(p, f)) : [p]));
  if (!files.length) { console.error("no .glb files given"); process.exit(2); }
  let errors = 0, warnings = 0;
  for (const f of files) {
    const r = await validator.validateBytes(new Uint8Array(fs.readFileSync(f)));
    errors += r.issues.numErrors;
    warnings += r.issues.numWarnings;
    if (r.issues.numErrors || r.issues.numWarnings) console.log(f, JSON.stringify(r.issues.messages.slice(0, 5)));
  }
  console.log(`${files.length} file(s): ${errors} error(s), ${warnings} warning(s)`);
  process.exit(errors ? 1 : 0);
})();
