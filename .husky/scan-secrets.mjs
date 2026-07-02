#!/usr/bin/env node
// 提交前敏感信息扫描器。
// 为什么这么做：仓库运行期会产生大量本地敏感数据（飞书身份 ID、应用密钥、
// PAT、带主机用户名的绝对路径等）。这些数据大多已被 .gitignore 忽略，但
// `git add -f` 可绕过忽略，或敏感值被复制粘贴进普通源码/文档。本脚本作为
// 提交前的最后一道防线：既拦截“落在敏感目录/文件里的内容”，也用“通用正则”
// 拦截“泄漏到任意文件里的敏感串”。
//
// 重要：本文件会被提交到远程，禁止写入任何真实敏感信息。下面所有规则都只
// 描述敏感串的“结构形状”，模式字符串本身不会命中自己（已逐条校验）。
//
// 选型：用 Node 而非纯 shell，是因为本工作区强约束 Node >=18 跨三端可用，
// 正则与 UTF-8 处理在三端行为一致，避免 grep/sed 的平台差异。

import { execFileSync } from 'node:child_process';

// --- 1. 直接禁止入库的敏感路径（相对仓库根） ---
// 这些位置承载运行时敏感数据；即便被 -f 强制暂存也必须拦截。前缀匹配。
const SENSITIVE_PATH_PREFIXES = [
  'msg-listener/.local',
  'msg-listener/.venv',
  'msg-listener/cache',
  'msg-listener/logs',
  'msg-listener/msg_listener_agent.egg-info',
  'msg-listener/config.toml',
];

// 与具体子项目无关的通用敏感物：任意层级命中即拦截。
const SENSITIVE_PATH_SEGMENTS = new Set(['.local', '.venv']);
const SENSITIVE_BASENAMES = new Set(['config.toml']);
const isEggInfoSegment = (seg) => seg.endsWith('.egg-info');

// --- 2. 通用敏感内容正则 ---
// 每条规则锚定真实敏感串的结构；顺序即报告顺序。
const CONTENT_RULES = [
  {
    name: '绝对家目录路径（暴露主机用户名）',
    // 形如 Users/<用户名>/ 或 home/<用户名>/（兼容 Windows 反斜杠与盘符前缀）。
    re: /[/\\](?:Users|home)[/\\][^/\\\s"'<>]+[/\\]/g,
  },
  {
    name: '飞书身份/会话 ID（open_id、chat_id 等）',
    re: /\b(?:ou|oc|on|om|og|ug|oi)_[0-9a-f]{16,}\b/g,
  },
  {
    name: '飞书应用 ID（app_id）',
    re: /\bcli_[0-9a-z]{12,}\b/g,
  },
  {
    name: 'kxcymc OpenAPI PAT',
    re: /\bcode_pat_[A-Za-z0-9]{8,}\b/g,
  },
  {
    name: 'AWS Access Key ID',
    re: /\bAKIA[0-9A-Z]{16}\b/g,
  },
  {
    name: 'Slack Token',
    re: /\bxox[baprs]-[0-9A-Za-z-]{10,}\b/g,
  },
  {
    name: 'GitHub Token',
    re: /\bgh[pousr]_[0-9A-Za-z]{20,}\b/g,
  },
  {
    name: 'JWT',
    re: /\beyJ[0-9A-Za-z_-]{10,}\.[0-9A-Za-z_-]{10,}\.[0-9A-Za-z_-]{6,}\b/g,
  },
  {
    name: '私钥文件头',
    re: /-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----/g,
  },
  {
    name: '凭证键值对（密钥/口令/令牌被赋值为带引号的长字符串）',
    // 仅当值是“带引号且长度>=8 的连续非空白串”时才判定，避免命中 `token = fn()` 这类普通代码。
    re: /\b(?:app_?secret|client_?secret|secret_?access_?key|secret_?key|access_?key_?id|api_?key|access_?token|refresh_?token|private_?key|passwd|password)\b\s*["']?\s*[:=]\s*["'][^"'\s]{8,}["']/gi,
  },
];

// 单文件扫描上限，跳过超大文件以保证提交速度。
const MAX_BYTES = 2 * 1024 * 1024;

function run(args) {
  return execFileSync('git', args, { maxBuffer: 256 * 1024 * 1024 });
}

// 本次提交暂存区里新增/修改/复制的文件（相对仓库根，NUL 分隔）。
function stagedFiles() {
  const buf = run(['diff', '--cached', '--name-only', '--diff-filter=ACM', '-z']);
  return buf.toString('utf8').split('\0').filter(Boolean);
}

// 读取暂存区（index）里的文件内容，而非工作区，确保扫的正是本次要提交的版本。
function stagedContent(file) {
  return run(['show', `:${file}`]);
}

// 敏感路径判定：命中返回原因，否则返回 null。
function pathReason(file) {
  const norm = file.replace(/\\/g, '/');
  for (const prefix of SENSITIVE_PATH_PREFIXES) {
    if (norm === prefix || norm.startsWith(`${prefix}/`)) {
      return `敏感路径前缀 ${prefix}`;
    }
  }
  const segs = norm.split('/');
  const base = segs[segs.length - 1];
  if (SENSITIVE_BASENAMES.has(base)) return `敏感文件名 ${base}`;
  for (const seg of segs) {
    if (SENSITIVE_PATH_SEGMENTS.has(seg)) return `敏感目录段 ${seg}`;
    if (isEggInfoSegment(seg)) return `敏感目录段 ${seg}`;
  }
  return null;
}

// 命中片段脱敏：只在本地终端打印，仍避免完整回显敏感值。
function mask(raw) {
  const s = raw.replace(/\s+/g, ' ').trim();
  if (s.length <= 12) return `${s.slice(0, 2)}***${s.slice(-2)}`;
  return `${s.slice(0, 6)}…***…${s.slice(-4)}`;
}

// 内容扫描：返回命中列表 [{ line, rule, snippet }]。二进制文件直接跳过。
function contentFindings(file) {
  let buf;
  try {
    buf = stagedContent(file);
  } catch {
    return [];
  }
  if (buf.length === 0 || buf.length > MAX_BYTES) return [];
  if (buf.includes(0)) return []; // 含 NUL 视为二进制

  const text = buf.toString('utf8');
  const lines = text.split(/\r?\n/);
  const findings = [];
  for (const rule of CONTENT_RULES) {
    for (let i = 0; i < lines.length; i += 1) {
      const line = lines[i];
      rule.re.lastIndex = 0;
      let m;
      while ((m = rule.re.exec(line)) !== null) {
        findings.push({ line: i + 1, rule: rule.name, snippet: mask(m[0]) });
        if (m.index === rule.re.lastIndex) rule.re.lastIndex += 1; // 防零宽死循环
      }
    }
  }
  return findings;
}

function main() {
  const files = stagedFiles();
  if (files.length === 0) process.exit(0);

  const problems = [];
  for (const file of files) {
    const reason = pathReason(file);
    if (reason) {
      problems.push({ file, kind: 'path', detail: reason });
      continue; // 路径本身就敏感，无需再扫内容
    }
    for (const f of contentFindings(file)) {
      problems.push({ file, kind: 'content', detail: `${f.rule} @L${f.line}: ${f.snippet}` });
    }
  }

  if (problems.length === 0) process.exit(0);

  const red = (s) => `\x1b[31m${s}\x1b[0m`;
  const yellow = (s) => `\x1b[33m${s}\x1b[0m`;
  console.error(red('\n✖ 提交被拦截：检测到疑似敏感信息\n'));
  const byFile = new Map();
  for (const p of problems) {
    if (!byFile.has(p.file)) byFile.set(p.file, []);
    byFile.get(p.file).push(p);
  }
  for (const [file, items] of byFile) {
    console.error(yellow(`  ${file}`));
    for (const it of items) {
      const tag = it.kind === 'path' ? '敏感路径' : '敏感内容';
      console.error(`    - [${tag}] ${it.detail}`);
    }
  }
  console.error(
    '\n处理建议：\n' +
      '  1. 敏感路径：从暂存区移除（git restore --staged <file>），确认已被 .gitignore 忽略。\n' +
      '  2. 敏感内容：删除或改用环境变量/占位符后重新提交。\n' +
      '  3. 误报：确认无敏感信息后，可用 git commit --no-verify 跳过本次检查（请谨慎）。\n',
  );
  process.exit(1);
}

main();
