# Frontend Unit Testing Roadmap

> Point-in-time inventory of what's tested, what's not, and the wave plan to close the gap.
> Blueprint for HOW to test: see `frontend-unit-testing-blueprint.md`
> Last updated: Feb 17, 2026

---

## Current State Audit

### Existing Coverage (67 test files)

| Area | Tested Files |
|---|---|
| `dynamic_forms/utils/` | `fieldRetentionUtils`, `reloadPropsUtils` (2 test files + trigger test) |
| `dynamic_forms/hooks/` | `useReloadProps` (2 test files), `useConditionalValueCleaner` |
| `dynamic_forms/` (integration) | `Container.formReinitialize`, `Container.reloadPropsIntegration`, `RecursiveContainer.reloadProps`, `Container.reloadProps.integration` |
| `ai-builder/` (integration) | `ActionRegistry`, `backward-compatibility`, `domain-context`, `chat-panel-thread-flow`, `apply-changes-action`, `thread-list-loading` |
| `ai-builder/hooks + utils` | `agentRouting`, `useWorkflowActions`, `useMessageInputVariableInsertion`, `useVariableDisplayFormatter`, `useChatMessages` |
| `node-config-ai/` | `hooks.ts` (4 hooks: `useNodeConfigSync`, `useNodeConfigActions`, `useNodeConfigActionRegistry`, `useNodeConfigDomainContext`), `useAIInputTypeManager` |
| `workflows/interactions + interface hooks` | `useWorkflowContextMenu`, `useExecutionThread`, `useListenerPolling`, `useFormValidation`, `state-initializers`, `useValidatedWorkflow`, `useInterfaceState`, `useInterfaceExecution`, `useInterfaceActions`, `useInterfaceConversation`, `useNodeDefinition` |
| `global hooks` | `useAxios`, `useAutoClose`, `useGlobalNavigation`, `usePageTimeTracking` |
| `stores` | `InterfaceFormStore`, `TenantStore`, `AuthStore`, `loadingUiStore`, `FilePreviewStore` |
| `shared/global utils` | `inputTypeUtils`, `inputTypeNormalizer`, `formUtils`, `columnIntersection`, `referenceTransform`, `blockNameUtils`, `navigationUtils`, `dynamic-options`, `formErrorUtils`, `fileReaderUtils`, `RunTimePreferenceUtils`, `workflow-utils`, `youtube-utils`, `feature-flags`, `random-float`, `lib/utils`, `lib/wrappers`, `remark-youtube-embed` |

### Deprecated Scope Note

- `reloadProps` feature is being deprecated. Related suites are intentionally skipped:
  - `components/dynamic_forms/utils/__tests__/reloadPropsUtils.test.ts`
  - `components/dynamic_forms/utils/__tests__/Container.reloadProps.integration.test.ts`

### Coverage Gaps

| Area | Untested Files | Priority |
|---|---|---|
| `dynamic_forms/utils/` | (covered) | COMPLETED |
| `dynamic_forms/hooks/` | `useSettingFieldDataSource` (708 lines), `useFormInitialization` (346 lines), `useReloadPropsManager`, `useInputTypeManager`, `useDynamicInputOptions`, `useFieldValidation`, `useMention`, `useServerErrorHandling` | CRITICAL |
| `ai-builder/utils/` | (covered) | COMPLETED |
| `ai-builder/hooks/` | (covered) | COMPLETED |
| `workflow helpers/` | `WorkflowUtils`, `WorkflowBlockUtils`, `WorkflowConditionUtils`, `WorkflowLayoutUtils`, `ColumnMetadataUtils`, `ExecutionWorkflowUtils`, `NodeInputMetadataExtractor` | HIGH |
| `workflow-interface/hooks/` | `useInterfaceConversation` polling edge cases, `useNodeDefinition` error branches | MEDIUM |
| `workflow hooks/` | `useNodeConfigAI`, `useDynamicFormStateManager` (214 lines) | HIGH |
| `workflow utils/` | `workflowBuilderUtils`, `aiChatUtils`, `nodeFormSaver`, `defaultValueApplier` | MEDIUM |
| `node-config-ai/hooks/` | `useAIFieldOptions` (275 lines) | MEDIUM |
| Global `hooks/` | (covered) | COMPLETED |
| Global `utils/` | (covered) | COMPLETED |
| Global `lib/` | (covered) | COMPLETED |
| Zustand stores | (covered) | COMPLETED |

---

## Wave Plan

### Prerequisites (Do First)

1. **Add coverage config to `vitest.config.ts`** — as defined in blueprint
2. **Create `tests/unit/builders/` directory** — for shared fixture builders
3. **Add CI scripts** — `test:unit:ci`, `test:unit:changed`
4. **Wire `test:unit:changed` into lefthook** — pre-commit hook

---

### Wave 1: Pure Functions (Highest ROI, Zero Mocks)

These are "helpers, validators, mappers" — pure input → output, no React context needed.

| # | File | ~Lines | Key Behaviors to Test |
|---|---|---|---|
| 1 | `components/dynamic_forms/utils/inputTypeNormalizer.ts` | 162 | Type normalization, SKIPPED_TYPES filtering, validator dispatch, `createLabelFromFieldType` |
| 2 | `components/dynamic_forms/hooks/inputTypeUtils.ts` | 100 | `hasMultipleInputTypes`, `getCurrentFieldConfig`, `findInputTypeArrayPosition`, `getInitialInputTypePosition` |
| 3 | `components/dynamic_forms/utils/formUtils.ts` | 34 | Form value extraction, normalization |
| 4 | `components/dynamic_forms/utils/columnIntersection.ts` | 33 | Column matching/intersection logic |
| 5 | `components/dynamic_forms/utils/referenceTransform.ts` | 67 | Reference data transformation |
| 6 | `components/dynamic_forms/utils/blockNameUtils.ts` | 48 | Block name generation/validation |
| 7 | `components/dynamic_forms/utils/navigationUtils.ts` | 43 | Navigation path computation |
| 8 | `components/dynamic_forms/utils/dynamic-options.ts` | 36 | Dynamic option resolution |
| 9 | `components/dynamic_forms/utils/fileReaderUtils.ts` | 46 | File reading utilities |
| 10 | `components/dynamic_forms/form-management/utils/formErrorUtils.ts` | 50 | Error formatting, error state derivation |
| 11 | `components/ai-builder/utils/agentRouting.ts` | 184 | URL routing by agent type, param building, request shaping |
| 12 | `app/workflows/.../helpers/WorkflowUtils.ts` | 200+ | Workflow state helpers |
| 13 | `app/workflows/.../helpers/WorkflowBlockUtils.ts` | 200+ | Block manipulation helpers |
| 14 | `app/workflows/.../helpers/WorkflowConditionUtils.ts` | 150 | Condition evaluation logic |
| 15 | `app/workflows/.../helpers/ColumnMetadataUtils.ts` | 100 | Column metadata extraction |
| 16 | `app/workflows/.../helpers/NodeInputMetadataExtractor.ts` | 100 | Node input parsing |
| 17 | `app/workflows/.../helpers/WorkflowLayoutUtils.ts` | 100 | Layout computation |
| 18 | `app/workflows/.../helpers/ExecutionWorkflowUtils.ts` | 100 | Execution state derivation |
| 19 | `app/workflows/.../utils/workflowBuilderUtils.ts` | 96 | Builder state helpers |
| 20 | `app/workflows/.../utils/defaultValueApplier.ts` | 56 | Default value application logic |
| 21 | `app/workflows/.../utils/nodeFormSaver.ts` | 73 | Form save preparation |
| 22 | `app/workflows/.../utils/RunTimePreferenceUtils.ts` | 13 | Runtime preference computation |
| 23 | `utils/workflow-utils.ts` | 116 | `getLayoutedElements`, node/edge creation |
| 24 | `utils/youtube-utils.ts` | 22 | YouTube URL parsing |
| 25 | `utils/feature-flags.ts` | 30 | Feature flag evaluation |
| 26 | `lib/utils.ts` | 73 | `cn()`, `formatDuration()`, `bindStoreActions()`, `compose()` |
| 27 | `lib/remark-youtube-embed.ts` | 50 | Markdown YouTube embed transformer |

**Estimated: ~27 test files | Mocks: Zero**

---

### Wave 2: Hooks with Business Logic

Test with `renderHook()`, minimal mocking (mostly `vi.fn()` for callbacks).

#### Tier A — Critical (>200 lines, complex logic)

| # | File | ~Lines | Key Behaviors |
|---|---|---|---|
| 1 | `components/dynamic_forms/hooks/useSettingFieldDataSource.ts` | 708 | Field data source resolution, dynamic options, async fetching |
| 2 | `components/dynamic_forms/hooks/useFormInitialization.ts` | 346 | Form initialization orchestration, field setup |
| 3 | `components/ai-builder/ai-conversation/hooks/useChatMessages.ts` | 349 | Message list management, optimistic updates |
| 4 | `app/workflows/.../hooks/useInterfaceState.ts` | 234 | Interface state machine transitions |
| 5 | `components/dynamic_forms/hooks/useNestedSearch.ts` | 221 | Nested object search logic |
| 6 | `app/workflows/.../hooks/usePublishWorkflowAsync.ts` | 218 | Publish workflow state machine |
| 7 | `app/workflows/.../components/workflows/hooks/useDynamicFormStateManager.ts` | 214 | Dynamic form state orchestration |
| 8 | `components/dynamic_forms/hooks/useUniversalSearch.ts` | 205 | Cross-field search logic |
| 9 | `app/workflows/.../components/node-config-ai/hooks/useAIFieldOptions.ts` | 275 | Field option computation for AI |

#### Tier B — High (100-200 lines)

| # | File | ~Lines | Key Behaviors |
|---|---|---|---|
| 10 | `components/dynamic_forms/hooks/useReloadPropsManager.ts` | 186 | Reload behavior for dynamic forms |
| 11 | `components/ai-builder/ai-conversation/hooks/useMessageInputVariableInsertion.ts` | 181 | Variable token insertion into message |
| 12 | `components/ai-builder/ai-conversation/hooks/useWorkflowActions.ts` | 179 | Workflow action dispatch |
| 13 | `components/dynamic_forms/hooks/useFieldOptionsWithSearch.ts` | 155 | Search/filtering in field options |
| 14 | `hooks/useAxios.ts` | 142 | Request lifecycle, error handling, cancellation (test ONCE as infra) |
| 15 | `components/dynamic_forms/hooks/useDynamicInputOptions.ts` | 135 | Dynamic input options resolution |
| 16 | `components/dynamic_forms/hooks/useInvalidValueCleaner.ts` | 130 | Invalid value cleaning logic |
| 17 | `app/workflows/.../hooks/useExecutionThread.ts` | 124 | Execution polling, state derivation |
| 18 | `components/dynamic_forms/utils/debouncedValidationManager.ts` | 119 | Debounce timing, validation queue |
| 19 | `app/workflows/.../hooks/useListenerPolling.ts` | 115 | Poll interval management, stop conditions |
| 20 | `app/workflows/.../components/node-config-ai/hooks/useAIInputTypeManager.ts` | 110 | AI input type selection state |

#### Tier C — Moderate (<100 lines)

| # | File | ~Lines | Key Behaviors |
|---|---|---|---|
| 21 | `app/workflows/.../hooks/useInterfaceExecution.ts` | 85 | Execution state management |
| 22 | `components/ai-builder/ai-generated-tabular-dataset/hooks.ts` | 82 | Tabular data transformation |
| 23 | `app/workflows/.../hooks/state-initializers.ts` | 80 | Initial state computation |
| 24 | `hooks/useAutoClose.ts` | 55 | Timer-based auto-close logic |
| 25 | `app/workflows/.../context/hooks/useValidatedWorkflow.ts` | 54 | Validation result derivation |
| 26 | `components/ai-builder/hooks/useVariableDisplayFormatter.ts` | 50 | Variable display formatting |
| 27 | `app/workflows/.../hooks/useFormValidation.ts` | 48 | Form validation rules, error state |

**Estimated: ~27 test files | Mocks: Low-Medium (mostly `vi.fn()` callbacks)**

---

### Wave 3: Zustand Stores (Parallel with Wave 2)

| # | Store | ~Lines | Key Behaviors |
|---|---|---|---|
| 1 | `app/store/InterfaceFormStore.ts` | 156 | Form state transitions, field value management |
| 2 | `app/store/TenantStore.ts` | 86 | Tenant switching logic |
| 3 | `app/workflows/.../context/store/loadingUiStore.ts` | 23 | Loading state coordination |
| 4 | `app/store/AuthStore.ts` | 18 | Auth state derivation |
| 5 | `components/ai-builder/store/FilePreviewStore.ts` | 20 | File preview state management |

**Estimated: ~5 test files | Mocks: Zero (Zustand stores testable without React)**

---

## Effort Summary

| Wave | Test Files | Mock Complexity | Timing |
|---|---|---|---|
| Prerequisites | 2-3 config files | N/A | **Do first** |
| Wave 1: Pure Functions | ~27 | None | **Start immediately** |
| Wave 2: Hooks | ~27 | Low-Medium | **After Wave 1** |
| Wave 3: Stores | ~5 | None | **Parallel with Wave 2** |

**Total: ~59 new test files**

---

## Retrofit Rules

> "Never touch a piece of code without first thinking about tests."

1. **New feature** → TDD from the start (see blueprint)
2. **Bug fix** → write the failing test FIRST, then fix
3. **Modifying existing untested code** → add tests for existing behavior before changing
4. **Wave 1 files touched in any PR** → must have tests before merge (pure functions are non-negotiable)
5. **Wave 2/3 files** → test when modifying, don't block PRs on pre-existing gaps

---

## Progress Tracking

Update this section as waves complete.

| Wave | Status | Files Done | Files Remaining |
|---|---|---|---|
| Prerequisites | Completed | 4 | 0 |
| Wave 1 | In progress | 17 | 10 |
| Wave 2 | In progress | 18 | 9 |
| Wave 3 | Completed | 5 | 0 |
