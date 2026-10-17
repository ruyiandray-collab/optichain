// Vitest 只跑前端测试；functions-tests/ 用 node 内置 test 运行（node --test functions-tests/qa.test.mjs）
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: { include: ['src/**/*.test.ts'] },
});
