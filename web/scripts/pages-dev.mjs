// 本地预览 Cloudflare Pages（静态 dist + functions/）。
// DeepSeek key 从环境变量读取；Windows 上会话启动后才设置的用户级变量，从注册表 HKCU\Environment 读取。
// 交叉核对发现：原来用 --binding 在命令行传 key，进程列表里可见。现在写入 wrangler 约定的 .dev.vars
// （已在 .gitignore 中），仅限本地，退出时删除；不打印 key。
import { spawn, execFileSync } from "node:child_process";
import { writeFileSync, rmSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const webDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const devVars = resolve(webDir, ".dev.vars");
let key = process.env.DEEPSEEK_API_KEY;
if (!key && process.platform === "win32") {
  try {
    const out = execFileSync("reg", ["query", String.raw`HKCU\Environment`, "/v", "DEEPSEEK_API_KEY"], { encoding: "utf8" });
    key = out.match(/DEEPSEEK_API_KEY\s+REG_\w+\s+(\S+)/)?.[1];
  } catch { /* 未设置 */ }
}
const cleanup = () => { try { rmSync(devVars, { force: true }); } catch { /* ignore */ } };
if (key) writeFileSync(devVars, `DEEPSEEK_API_KEY=${key}\n`, { mode: 0o600 });
const port = process.env.PORT || "8788";
console.log(`pages dev on :${port} (DeepSeek key ${key ? "found" : "missing — /api/ask will report unavailable"})`);
const child = spawn(process.platform === "win32" ? "npx.cmd" : "npx", ["wrangler", "pages", "dev", "--port", port],
  { cwd: webDir, stdio: "inherit", shell: process.platform === "win32" });
for (const sig of ["SIGINT", "SIGTERM"]) process.on(sig, () => { cleanup(); child.kill(sig); });
child.on("exit", (code) => { cleanup(); process.exit(code ?? 0); });
