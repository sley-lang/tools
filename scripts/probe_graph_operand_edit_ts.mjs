import { openRuntime, SleyToolsError } from '../packages/typescript/src/index.js';

const [tools, core, project] = process.argv.slice(2);
const samples = [
  [0, 0, 10], [1, 0, 10], [10, 0, 10], [11, 0, 10],
  [-1, 0, 10], [0, 1, 0], [5, 5, 5],
];
function expected(value, lower, upper) {
  if (lower > upper) return { within_range: false, invalid_bounds: true, below_lower: false, above_upper: false };
  const below = value < lower;
  const above = value > upper;
  return { within_range: !(below || above), invalid_bounds: false, below_lower: below, above_upper: above };
}
const cases = samples.map(([value, lower, upper]) => ({
  id: `v${value}l${lower}u${upper}`,
  inputs: { value, lower, upper }, expected: expected(value, lower, upper),
}));
const runtime = openRuntime({ tools, core });
try {
  const program = await runtime.openProject({ project });
  const oldRoot = program.stateRoot;
  let invalidRejected = false;
  try {
    await program.proposeGraphOperand('valid_bounds', 'above', 'right', 'bad.upper', cases);
  } catch (error) {
    invalidRejected = error instanceof SleyToolsError && error.code === 'ST_INPUT_INVALID';
  }
  if (!invalidRejected) throw new Error('TypeScript accepted an invalid operand reference');
  const proposed = await program.proposeGraphOperand('valid_bounds', 'above', 'right', 'param.upper', cases);
  await program.validate(proposed.candidate_id);
  const committed = await program.commit(proposed.candidate_id);
  if (committed.new_root === oldRoot || committed.changed_case_count !== 2
    || committed.external_assertions !== samples.length) {
    throw new Error('TypeScript operand edit did not change native behavior');
  }
  for (const [value, lower, upper] of samples) {
    const observed = await program.callGraph('evaluate', { inputs: { value, lower, upper } });
    if (JSON.stringify(observed.value) !== JSON.stringify(expected(value, lower, upper))) {
      throw new Error('TypeScript observed a wrong native operand result');
    }
  }
  process.stdout.write(`${JSON.stringify({ old_root: oldRoot, new_root: committed.new_root,
    candidate_id: proposed.candidate_id, native_cases: samples.length, invalid_reference_rejected: true })}\n`);
} finally {
  await runtime.close();
}
