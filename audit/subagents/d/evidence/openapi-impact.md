# Current OpenAPI drift impact assessment

The regenerated artifact differs from the committed mini-program type file by
502 insertions and one deletion (`audit/api-contract/types.diff`, 579 diff
lines). This is a confirmed contract-governance failure, but the present delta
does not by itself prove that a currently executed page crashes.

Observed delta and active impact:

| Delta                                                                                         | Active mini-program use                                                                             | Assessment                                                                                                                                                                                                                                       |
| --------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `AnswerSubmission.taskVersion`, `PracticeTask.taskVersion`, and new `PracticeQuestion*` types | No formal mini-program wrapper imports or calls the legacy generated `/api/v1/practice` contract    | No current runtime failure reproduced                                                                                                                                                                                                            |
| New Hub Bot authorization schema and four operations                                          | `components/ilink-bot-sheet/index.ts` actively calls these endpoints through `api/ilink-channel.ts` | Active path bypasses the generated contract with a manual interface; the manual interface is stricter (`string \| null`) than the current OpenAPI optional fields. No bad payload was exercised because the only configured origin is production |
| `RecognizeMathInkCommand.questionElapsedMs` and `.sessionAtMs`                                | `pages/practice/index.ts` calls `api/math-ink.ts`                                                   | Manual command already includes and sends both optional timing fields, so this particular stale generated type does not suppress the runtime fields                                                                                              |

Assurance implication:

- TypeScript passing does not establish current backend compatibility for the
  Hub Bot and Math Ink paths because both paths redeclare their contracts
  manually.
- The documented `api:check` command is not present in package scripts and the
  normal `check` script does not execute the implemented `--check` mode.
- This observation should be merged into the baseline/OpenAPI root finding,
  rather than filed again as a D-subaudit runtime defect.

Evidence:

- `audit/api-contract/types.diff`
- `apps/wechat/miniprogram/api/ilink-channel.ts`
- `apps/wechat/miniprogram/components/ilink-bot-sheet/index.ts`
- `apps/wechat/miniprogram/api/math-ink.ts`
- `apps/wechat/miniprogram/pages/practice/index.ts`
- `apps/wechat/package.json`
- `apps/wechat/README.md`
- `apps/wechat/scripts/generate-api.mjs`
