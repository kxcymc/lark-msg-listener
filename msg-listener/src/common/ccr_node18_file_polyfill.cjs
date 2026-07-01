// Node 18 兼容垫片：claude-code-router 打包的 undici@7 期望全局 File，
// 但 File 在 Node 20 才成为全局对象，Node 18 仅在 node:buffer 暴露，
// 导致 ccr 在 Node 18 下启动即抛 `ReferenceError: File is not defined`。
// 经 NODE_OPTIONS=--require 预加载本垫片即可修复；Node 20+ 上 globalThis.File
// 已存在，此处为无副作用的空操作，故可对所有 ccr 子进程无条件注入。
if (typeof globalThis.File === "undefined") {
  try {
    const { File } = require("node:buffer");
    if (File) {
      globalThis.File = File;
    }
  } catch (_) {
    // node:buffer 不可用（极旧 Node）时静默跳过；ccr 本身也不支持这种环境。
  }
}
