// src/utils/__tests__/exec.test.ts

import { execute_argv } from '../exec';

describe('execute_argv — shell metacharacters are inert', () => {
  it('passes ";" and a chained command as a single literal argument', async () => {
    // Under a shell, `echo hello; whoami` would ALSO run `whoami`. With execFile
    // there is no shell, so the whole string is one literal argv element.
    const result = await execute_argv('echo', ['hello; whoami']);
    expect(result.exitCode).toBe(0);
    expect(result.stdout.trim()).toBe('hello; whoami');
  });

  it('does not perform command substitution $(...)', async () => {
    const result = await execute_argv('echo', ['$(whoami)']);
    expect(result.exitCode).toBe(0);
    expect(result.stdout.trim()).toBe('$(whoami)');
  });

  it('does not interpret a pipe', async () => {
    const result = await execute_argv('echo', ['a | b']);
    expect(result.exitCode).toBe(0);
    expect(result.stdout.trim()).toBe('a | b');
  });

  it('does not interpret backticks', async () => {
    const result = await execute_argv('echo', ['`whoami`']);
    expect(result.exitCode).toBe(0);
    expect(result.stdout.trim()).toBe('`whoami`');
  });

  it('propagates a non-zero exit code', async () => {
    const result = await execute_argv('false', []);
    expect(result.exitCode).toBe(1);
  });

  it('returns exit code 1 when the program does not exist (ENOENT)', async () => {
    const result = await execute_argv('this-binary-does-not-exist-xyz', []);
    expect(result.exitCode).toBe(1);
  });

  it('mirrors the CommandResult shape of execute_command', async () => {
    const result = await execute_argv('echo', ['ok']);
    expect(result).toHaveProperty('stdout');
    expect(result).toHaveProperty('stderr');
    expect(result).toHaveProperty('exitCode');
  });
});
