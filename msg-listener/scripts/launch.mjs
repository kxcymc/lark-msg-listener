import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  createWriteStream,
  existsSync,
  mkdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import https from "node:https";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const PROJECT_ROOT = path.resolve(__dirname, "..");
const PYTHON_VERSION = "3.11";
const UV_INSTALL_DIR = path.join(PROJECT_ROOT, ".local", "uv");
const UV_LOCK_PATH = path.join(PROJECT_ROOT, "uv.lock");
const VENV_PATH = path.join(PROJECT_ROOT, ".venv");
const DEPS_STAMP_PATH = path.join(PROJECT_ROOT, ".venv", ".uv-lock.stamp");
const LOCAL_CA_BUNDLE_PATH = path.join(PROJECT_ROOT, ".local", "certs", "system-ca-bundle.pem");
const PEM_BEGIN = "-----BEGIN CERTIFICATE-----";
const PEM_END = "-----END CERTIFICATE-----";

function fail(message) {
  console.error(`[launcher] ${message}`);
  process.exit(1);
}

function run(command, args, options = {}) {
  const label = [command, ...args].join(" ");
  console.log(`[launcher] $ ${label}`);
  const result = spawnSync(command, args, {
    stdio: "inherit",
    cwd: PROJECT_ROOT,
    env: process.env,
    ...options,
  });
  if (result.error) {
    fail(`执行失败：${result.error.message}`);
  }
  if ((result.status ?? 1) !== 0) {
    process.exit(result.status ?? 1);
  }
}

function dedupePemCertificates(pem) {
  const certs = [];
  const seen = new Set();
  let start = 0;
  while (true) {
    const begin = pem.indexOf(PEM_BEGIN, start);
    if (begin < 0) {
      break;
    }
    let end = pem.indexOf(PEM_END, begin);
    if (end < 0) {
      break;
    }
    end += PEM_END.length;
    const block = pem.slice(begin, end).trim();
    start = end;
    if (!seen.has(block)) {
      seen.add(block);
      certs.push(block);
    }
  }
  return certs;
}

function readIfExists(filePath) {
  return existsSync(filePath) ? readFileSync(filePath, "utf8") : "";
}

function readMacosKeychainCertificates() {
  if (process.platform !== "darwin") {
    return "";
  }
  const commands = [
    ["security", "find-certificate", "-a", "-p"],
    [
      "security",
      "find-certificate",
      "-a",
      "-p",
      "/System/Library/Keychains/SystemRootCertificates.keychain",
      "/Library/Keychains/System.keychain",
    ],
  ];
  return commands
    .map((args) =>
      spawnSync(args[0], args.slice(1), {
        encoding: "utf8",
        stdio: ["ignore", "pipe", "ignore"],
      })
    )
    .filter((result) => result.status === 0 && result.stdout)
    .map((result) => result.stdout)
    .join("\n");
}

function readWindowsRootCertificates() {
  if (process.platform !== "win32") {
    return "";
  }
  // certutil -store Root 输出包含 PEM 块（带 BEGIN/END CERTIFICATE）。
  const result = spawnSync("certutil", ["-store", "Root"], {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "ignore"],
  });
  return result.status === 0 && result.stdout ? result.stdout : "";
}

function readLinuxSystemCaBundle() {
  if (process.platform === "darwin" || process.platform === "win32") {
    return "";
  }
  const candidates = [
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ssl/cert.pem",
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",
  ];
  for (const candidate of candidates) {
    if (existsSync(candidate)) {
      return readFileSync(candidate, "utf8");
    }
  }
  return "";
}

function ensureTlsCaEnv() {
  if (process.env.REQUESTS_CA_BUNDLE || process.env.SSL_CERT_FILE) {
    const explicitBundle = process.env.REQUESTS_CA_BUNDLE || process.env.SSL_CERT_FILE;
    process.env.NODE_EXTRA_CA_CERTS ||= explicitBundle;
    return explicitBundle;
  }

  let basePem = "";
  if (process.platform === "darwin") {
    basePem = [
      readIfExists("/etc/ssl/cert.pem"),
      readIfExists("/opt/homebrew/etc/openssl@3/cert.pem"),
      readMacosKeychainCertificates(),
    ].join("\n");
  } else if (process.platform === "win32") {
    basePem = readWindowsRootCertificates();
  } else {
    basePem = readLinuxSystemCaBundle();
  }

  const certs = dedupePemCertificates(basePem);
  if (certs.length === 0) {
    return null;
  }

  mkdirSync(path.dirname(LOCAL_CA_BUNDLE_PATH), { recursive: true });
  writeFileSync(LOCAL_CA_BUNDLE_PATH, `${certs.join("\n")}\n`, "utf8");
  process.env.REQUESTS_CA_BUNDLE = LOCAL_CA_BUNDLE_PATH;
  process.env.SSL_CERT_FILE = LOCAL_CA_BUNDLE_PATH;
  process.env.NODE_EXTRA_CA_CERTS ||= LOCAL_CA_BUNDLE_PATH;
  console.log(`[launcher] 已启用系统 TLS CA bundle: ${LOCAL_CA_BUNDLE_PATH}`);
  return LOCAL_CA_BUNDLE_PATH;
}

function httpsRequestOptions() {
  const caPath = process.env.NODE_EXTRA_CA_CERTS || process.env.SSL_CERT_FILE || process.env.REQUESTS_CA_BUNDLE;
  if (!caPath || !existsSync(caPath)) {
    return {};
  }
  return { ca: readFileSync(caPath) };
}

function signalExitCode(signal) {
  const signalNumbers = {
    SIGINT: 2,
    SIGTERM: 15,
    SIGHUP: 1,
    SIGQUIT: 3,
  };
  const number = signalNumbers[signal];
  return typeof number === "number" ? 128 + number : 1;
}

async function runManaged(command, args, options = {}) {
  const label = [command, ...args].join(" ");
  console.log(`[launcher] $ ${label}`);

  const child = spawn(command, args, {
    stdio: "inherit",
    cwd: PROJECT_ROOT,
    env: process.env,
    ...options,
  });

  let forwardedSignal = null;
  const handledSignals = ["SIGINT", "SIGTERM", "SIGHUP", "SIGQUIT"];
  const listeners = new Map();

  const cleanup = () => {
    for (const [signalName, listener] of listeners.entries()) {
      process.off(signalName, listener);
    }
    listeners.clear();
  };

  const waitForExit = new Promise((resolve, reject) => {
    child.once("error", (error) => {
      cleanup();
      reject(error);
    });
    child.once("exit", (code, signal) => {
      cleanup();
      resolve({ code, signal });
    });
  });

  for (const signalName of handledSignals) {
    const listener = () => {
      if (forwardedSignal == null) {
        forwardedSignal = signalName;
      }
      if (!child.killed) {
        try {
          child.kill(signalName);
        } catch {
          // ignore
        }
      }
    };
    listeners.set(signalName, listener);
    process.on(signalName, listener);
  }

  const result = await waitForExit;
  if (result.signal) {
    process.exit(signalExitCode(result.signal));
  }
  if ((result.code ?? 1) !== 0) {
    process.exit(result.code ?? signalExitCode(forwardedSignal));
  }
}

function download(url, destination) {
  return new Promise((resolve, reject) => {
    const request = https.get(url, httpsRequestOptions(), (response) => {
      if (
        response.statusCode &&
        response.statusCode >= 300 &&
        response.statusCode < 400 &&
        response.headers.location
      ) {
        response.resume();
        download(response.headers.location, destination).then(resolve).catch(reject);
        return;
      }
      if (response.statusCode !== 200) {
        reject(new Error(`下载失败，HTTP ${response.statusCode ?? "unknown"}`));
        return;
      }
      const file = createWriteStream(destination);
      response.pipe(file);
      file.on("finish", () => file.close(resolve));
      file.on("error", reject);
    });
    request.on("error", reject);
  });
}

function defaultUvCandidates() {
  const home = process.env.HOME || process.env.USERPROFILE || "";
  const candidates = [];
  if (process.platform === "win32") {
    candidates.push(path.join(UV_INSTALL_DIR, "uv.exe"));
    if (home) {
      candidates.push(path.join(home, ".local", "bin", "uv.exe"));
    }
  } else {
    candidates.push(path.join(UV_INSTALL_DIR, "uv"));
    if (home) {
      candidates.push(path.join(home, ".local", "bin", "uv"));
    }
  }
  return candidates;
}

function findUv() {
  const command = process.platform === "win32" ? "where" : "which";
  const lookup = spawnSync(command, ["uv"], { stdio: "pipe", encoding: "utf8" });
  if (lookup.status === 0) {
    const match = (lookup.stdout || "")
      .split(/\r?\n/)
      .map((item) => item.trim())
      .find(Boolean);
    if (match) {
      return match;
    }
  }
  return defaultUvCandidates().find((candidate) => existsSync(candidate)) || null;
}

async function installUv() {
  console.log("[launcher] 未检测到 uv，开始安装");
  const tempDir = path.join(tmpdir(), `msg-listener-agent-${process.pid}`);
  mkdirSync(tempDir, { recursive: true });
  mkdirSync(UV_INSTALL_DIR, { recursive: true });

  if (process.platform === "win32") {
    const scriptPath = path.join(tempDir, "install-uv.ps1");
    await download("https://astral.sh/uv/install.ps1", scriptPath);
    run("powershell.exe", [
      "-NoProfile",
      "-ExecutionPolicy",
      "Bypass",
      "-File",
      scriptPath,
    ], {
      env: {
        ...process.env,
        UV_INSTALL_DIR,
      },
    });
  } else {
    const scriptPath = path.join(tempDir, "install-uv.sh");
    await download("https://astral.sh/uv/install.sh", scriptPath);
    run("sh", [scriptPath], {
      env: {
        ...process.env,
        UV_INSTALL_DIR,
      },
    });
  }

  rmSync(tempDir, { recursive: true, force: true });
  const uv = findUv();
  if (!uv) {
    fail("uv 安装完成后仍未找到可执行文件，请手动安装 uv 后重试。");
  }
  return uv;
}

async function ensureUv() {
  return findUv() || (await installUv());
}

function ensureManagedPython(uvPath) {
  run(uvPath, ["python", "install", PYTHON_VERSION]);
}

function ensureVenv(uvPath) {
  const pythonPath = venvPythonPath();
  if (existsSync(pythonPath)) {
    return;
  }
  run(uvPath, ["venv", "--python", PYTHON_VERSION, ".venv"]);
}

function venvPythonPath() {
  return process.platform === "win32"
    ? path.join(PROJECT_ROOT, ".venv", "Scripts", "python.exe")
    : path.join(PROJECT_ROOT, ".venv", "bin", "python");
}

function lockStamp() {
  return createHash("sha256")
    .update(PYTHON_VERSION)
    .update("\n")
    .update(readFileSync(UV_LOCK_PATH, "utf8"))
    .digest("hex");
}

function readInstalledDepsStamp() {
  if (!existsSync(DEPS_STAMP_PATH)) {
    return null;
  }
  return readFileSync(DEPS_STAMP_PATH, "utf8").trim() || null;
}

function writeInstalledDepsStamp(stamp) {
  writeFileSync(DEPS_STAMP_PATH, `${stamp}\n`, "utf8");
}

function ensurePythonDeps(uvPath, pythonPath) {
  if (!existsSync(UV_LOCK_PATH)) {
    fail(`未找到 ${UV_LOCK_PATH}，请先执行 \`uv lock --native-tls\` 生成锁文件后再启动。`);
  }
  const stamp = lockStamp();
  if (readInstalledDepsStamp() === stamp) {
    console.log("[launcher] 检查 Python 依赖：uv.lock 未变更，跳过");
    return;
  }
  console.log("[launcher] 检查 Python 依赖：使用 uv.lock 同步");
  run(uvPath, [
    "sync",
    "--frozen",
    "--no-install-project",
    "--native-tls",
    "--python",
    pythonPath,
  ], {
    env: {
      ...process.env,
      UV_PROJECT_ENVIRONMENT: VENV_PATH,
    },
  });
  writeInstalledDepsStamp(stamp);
}

function parseMode(argv) {
  const modeIndex = argv.indexOf("--mode");
  if (modeIndex >= 0) {
    const mode = argv[modeIndex + 1];
    if (mode === "bootstrap" || mode === "run") {
      return {
        mode,
        passthrough: [
          ...argv.slice(0, modeIndex),
          ...argv.slice(modeIndex + 2),
        ],
      };
    }
    fail("--mode 仅支持 bootstrap 或 run");
  }
  return { mode: "run", passthrough: argv };
}

export async function launch(mode, passthroughArgs = []) {
  ensureTlsCaEnv();
  const uvPath = await ensureUv();
  ensureManagedPython(uvPath);
  ensureVenv(uvPath);
  const python = venvPythonPath();
  ensurePythonDeps(uvPath, python);
  const scriptPath = path.join(PROJECT_ROOT, "scripts", "setup.py");
  await runManaged(python, [scriptPath, "--mode", mode, ...passthroughArgs]);
}

if (process.argv[1] && path.resolve(process.argv[1]) === __filename) {
  const { mode, passthrough } = parseMode(process.argv.slice(2));
  await launch(mode, passthrough);
}
