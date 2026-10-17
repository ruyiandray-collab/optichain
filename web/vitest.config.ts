// Vitest 只跑前端测试；functions-tests/ 用 node 内置 test 运行（node --test functions-tests/qa.test.mjs）
import { defineConfig } from 'vitest/config';

export default defineConfig({
  // 整站 SSR 测试要渲染全部 38 家公司；机器忙时（本地预览同时在跑）偶尔超过默认 5 秒
  test: { include: ['src/**/*.test.ts'], testTimeout: 20000 },
});
