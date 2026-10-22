// Compile FlashLiquidator.sol with solc-js using the project's remappings (OZ from npm). Prints errors and writes an artifact.
const fs = require("fs"), path = require("path"), solc = require("solc");
const root = require("path").resolve(__dirname, "..", "contracts");
const srcDir = path.join(root, "src");
const files = {};
function add(p, name) { files[name] = { content: fs.readFileSync(p, "utf8") }; }
for (const f of fs.readdirSync(srcDir)) if (f.endsWith(".sol")) add(path.join(srcDir, f), "src/" + f);
for (const f of fs.readdirSync(path.join(srcDir, "interfaces"))) add(path.join(srcDir, "interfaces", f), "src/interfaces/" + f);
function findImports(p) {
  const oz = "@openzeppelin/contracts/";
  if (p.startsWith(oz)) { try { return { contents: fs.readFileSync(path.join(process.env.OZ_NODE_MODULES || path.join(__dirname, "..", "contracts", "node_modules"), p), "utf8") }; } catch (e) { return { error: "not found " + p }; } }
  if (files[p]) return files[p];
  const local = path.join(root, p); if (fs.existsSync(local)) return { contents: fs.readFileSync(local, "utf8") };
  return { error: "not found " + p };
}
const input = { language: "Solidity", sources: files, settings: { optimizer: { enabled: true, runs: 200 }, evmVersion: "cancun", viaIR: false,
  outputSelection: { "*": { "*": ["abi", "evm.bytecode.object", "evm.deployedBytecode.object"] } } } };
const out = JSON.parse(solc.compile(JSON.stringify(input), { import: findImports }));
const errs = (out.errors || []).filter(e => e.severity === "error"), warns = (out.errors || []).filter(e => e.severity !== "error");
for (const e of errs) console.log("ERROR:", e.formattedMessage);
for (const w of warns) console.log("warning:", (w.formattedMessage || "").split("\n")[0]);
if (!errs.length) {
  fs.mkdirSync(path.join(root, "out-solcjs"), { recursive: true });
  for (const name of ["FlashLiquidator", "CometLiquidator", "FlashArb"]) {
    const c = out.contracts["src/" + name + ".sol"][name];
    fs.writeFileSync(path.join(root, "out-solcjs", name + ".json"), JSON.stringify({ abi: c.abi, bytecode: "0x" + c.evm.bytecode.object, deployedBytecode: "0x" + c.evm.deployedBytecode.object, compiler: "solc-js " + solc.version(), settings: input.settings }, null, 1));
    console.log("OK", name, "bytecode bytes:", c.evm.bytecode.object.length / 2, "abi entries:", c.abi.length);
  }
}
