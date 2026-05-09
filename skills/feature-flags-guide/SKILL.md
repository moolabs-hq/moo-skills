---
name: feature-flags-guide
description: Frontend feature-flag patterns for the Moolabs codebase — when to flag, where to flag (highest branch point only), the three correct patterns (component swap, prop variation, route-level swap), the four anti-patterns (scattering, prop-drilling, hooks, compound flags), the lifecycle from creation to mandatory cleanup, and the PR review checklist. Use whenever the user is adding, removing, reviewing, or asking about a feature flag in the frontend, or asking "should this be behind a flag", "how do I add a feature flag", "where should I put this flag", "is this the right way to use a flag", or working on `utils/feature-flags.ts`. Triggers on: feature flag, FEATURE_FLAG, flag rollout, tenant-scoped flag, NEXT_PUBLIC_*, V2 component, flag cleanup.
---

# Feature Flags Guide — Frontend

## Purpose

This document defines **when, where, and how** to use feature flags in our frontend codebase. The goal is to enable merging feature branches to `main` early (behind a flag) so that merge conflicts are minimized across the team, while keeping the codebase clean and the flag footprint minimal.


---

## Our Feature Flag Utility

Located at `utils/feature-flags.ts`. Two patterns exist:

### 1. Global Boolean Flags

For features toggled globally across all tenants via environment variables.

```ts
// In utils/feature-flags.ts
const _FEATURE_FLAG_CONFIG: Record<string, string> = {
    MY_NEW_FEATURE: process.env.NEXT_PUBLIC_IS_MY_NEW_FEATURE_VISIBLE || "false"
};
```

Usage in components:

```tsx
import { FEATURE_FLAG } from "@/utils/feature-flags";

// Single branch point — see "Where to Place the Flag" below
{FEATURE_FLAG.MY_NEW_FEATURE ? <NewComponent /> : <OldComponent />}
```

### 2. Tenant-Scoped Flags

For features rolled out to specific tenants via comma-separated tenant ID lists.

```ts
// In utils/feature-flags.ts
export const isMyFeatureEnabled = (tenantId: string | number | null | undefined): boolean => {
    const allowedTenants = process.env.NEXT_PUBLIC_MY_FEATURE_VISIBLE_TENANTS;
    if (!allowedTenants) return false;
    if (!tenantId) return false;
    const allowedTenantIds = allowedTenants.split(",").map((id) => id.trim());
    return allowedTenantIds.includes(String(tenantId));
};
```

**Choose global boolean** when the feature is all-or-nothing per environment (dev/staging/prod). **Choose tenant-scoped** when you need to roll out gradually to specific customers.


---

## The One Rule: Flag at the Highest Branch Point

> **A feature flag should exist in exactly ONE place per feature — at the highest component boundary where the old and new behavior diverge.**

This is the single most important rule. Everything below follows from it.

### What This Means

Think of your component tree as a river. Don't place tiny dams (flags) at every tributary. Place ONE switch at the point where the river forks into old-path and new-path.

```
Page
 +-- Container        <-- FLAG HERE (one place)
      |
      +-- (flag=off) OldFeature
      |     +-- OldChildA
      |     +-- OldChildB
      |
      +-- (flag=on)  NewFeature
            +-- NewChildA
            +-- NewChildB
```

**NOT** like this:

```
Page
 +-- Container
      +-- Header          <-- flag here
      +-- Sidebar         <-- flag here
      +-- Content         <-- flag here
      +-- Footer          <-- flag here
      +-- Modal           <-- flag here
```


---

## Correct Patterns (with codebase examples)

### Pattern 1: Component Swap — Use for Medium to Large Features

When a feature changes the behavior/layout of a component significantly (e.g., drawer becomes modal, form gets a new layout), **create a parallel component and swap at the container level.**

Real example from our codebase — `NODE_CONFIG_AI_ASSISTANT` flag in `Container.tsx`:

```tsx
// components/dynamic_forms/Container.tsx
// FLAG is checked ONCE at the container level
{FEATURE_FLAG.NODE_CONFIG_AI_ASSISTANT && (
    <div className="px-6 pt-6 pb-3">
        <NodeConfigAssistant ... />
    </div>
)}
```

And the corresponding hide of the old UI in `FormContent.tsx` and `WorkflowEditorSidebar.tsx` uses `!FEATURE_FLAG.NODE_CONFIG_AI_ASSISTANT`. This is acceptable because the flag controls **show/hide of distinct sections**, not behavioral branching inside a single component.

**For larger features**, prefer a full component swap:

```tsx
// Good — one flag, one branch, two independent components
const NodeConfigContainer = () => {
    return FEATURE_FLAG.NODE_CONFIG_V2
        ? <NodeConfigV2 />
        : <NodeConfig />;
};
```

`NodeConfigV2` is a **new folder** with its own `index.tsx`, `hooks.ts`, `types.ts`, and `components/`. It shares nothing with the old component internally. When the flag is removed, delete the old folder.

### Pattern 2: Prop Variation — Use for Small Changes

When the change is minor (different label, different callback, different style), push the variation up to the parent. The child component remains flag-unaware.

```tsx
// Parent owns the flag, child is clean
const WorkflowActions = () => {
    const handleAction = FEATURE_FLAG.NEW_WORKFLOW_ACTIONS
        ? openModal
        : openDrawer;

    return (
        <ActionButton
            onPress={handleAction}
            label={FEATURE_FLAG.NEW_WORKFLOW_ACTIONS ? "Configure" : "Edit"}
        />
    );
};
```

The flag is in ONE place (the parent). `ActionButton` has zero knowledge of flags.

### Pattern 3: Route/Page-Level Swap — Use for Full-Page Overhauls

When an entire page is being redesigned:

```tsx
// app/workflows/page.tsx
const WorkflowsPage = () => {
    return FEATURE_FLAG.WORKFLOWS_V2
        ? <WorkflowsPageV2 />
        : <WorkflowsPageV1 />;
};
```

Temporary code duplication between V1 and V2 is acceptable and expected. It is cleaned up when the flag is removed.


---

## Anti-Patterns — What NOT to Do

### 1. Scattering Flags Inside a Component

```tsx
// BAD — flag checked in 5 places inside one component
const MyComponent = () => {
    const isV2 = FEATURE_FLAG.MY_FEATURE;

    return (
        <div>
            {isV2 ? <NewHeader /> : <OldHeader />}
            <Content mode={isV2 ? "new" : "old"} />
            {isV2 && <ExtraSection />}
            <Footer variant={isV2 ? "compact" : "full"} />
            {!isV2 && <LegacyBanner />}
        </div>
    );
};
```

**Why it is wrong:** 5 branch points for one feature. Impossible to reason about. Hard to remove cleanly. The component is now a tangled mix of two features.

**Fix:** Create `MyComponentV2` and swap at the parent.

### 2. Passing Flags as Props

```tsx
// BAD — flag leaks into child component API
<UserProfile isNewLayout={FEATURE_FLAG.PROFILE_V2} />
```

**Why it is wrong:** The child now has a prop that only exists for a temporary flag. It pollutes the component's interface and bleeds flag awareness downward.

**Fix:** Swap the entire component at the parent level, or use Pattern 2 (prop variation) where the prop is a meaningful value (like a callback or label), not the flag itself.

### 3. Flag in Business Logic / Hooks

```tsx
// BAD — flag buried inside a hook
const useWorkflowData = () => {
    const isV2 = FEATURE_FLAG.WORKFLOW_V2;
    const endpoint = isV2 ? "/api/v2/workflows" : "/api/v1/workflows";
    const transform = isV2 ? transformV2 : transformV1;
    // ...
};
```

**Why it is wrong:** The flag is hidden. Someone reading the component has no idea the hook behaves differently based on a flag. Debugging becomes harder.

**Fix:** Create a separate hook (`useWorkflowDataV2`) and select at the component level:

```tsx
const Container = () => {
    const data = FEATURE_FLAG.WORKFLOW_V2
        ? useWorkflowDataV2()
        : useWorkflowData();
    // Note: conditional hooks require the component-swap pattern
    // since hooks can't be called conditionally.
    // So actually — swap the whole component.
};
```

Since React hooks cannot be called conditionally, this naturally forces you toward the component-swap pattern, which is the correct approach anyway.

### 4. Nested / Compound Flag Checks

```tsx
// BAD — multiple flags interacting
if (FEATURE_FLAG.FEATURE_A && FEATURE_FLAG.FEATURE_B) {
    // What state is this? A on + B on? What about A on + B off?
}
```

**Why it is wrong:** Creates a matrix of states (2 flags = 4 states, 3 flags = 8 states). Untestable. Unpredictable.

**Fix:** Each flag should control an independent component boundary. If two features interact, they should be part of the same flag or one should depend on the other being fully shipped (flag removed).


---

## Decision Flowchart

When you need to add a feature flag, ask yourself:

```
Is the change limited to a single prop value (label, callback, style)?
  YES --> Pattern 2: Prop Variation (flag in parent, clean props to child)
  NO  |
      v
Does the change affect a full page or route?
  YES --> Pattern 3: Route-Level Swap (V1 page vs V2 page)
  NO  |
      v
Does the change affect a component's structure, layout, or behavior?
  YES --> Pattern 1: Component Swap (ComponentV2 folder, swap at container)
```

In all cases: **the flag import and check should appear in at most 1-3 places**, and those places should be **container/parent components**, never deep children.


---

## Lifecycle of a Feature Flag

### 1. Creation

* Add the env variable and flag entry in `utils/feature-flags.ts`
* Add the env variable to `.env.example` with default value `"false"`
* Add a test in `utils/__tests__/feature-flags.test.ts`
* Use the flag at the highest branch point (follow patterns above)

### 2. Staging / Testing

* Set the env variable to `"true"` in the staging environment
* Both old and new paths should be functional — QA can test by toggling the env var

### 3. Production Rollout

* Set to `"true"` in production (or add tenant IDs for tenant-scoped flags)

### 4. Cleanup (MANDATORY)

Once the feature is confirmed stable in production:

* Remove the flag from `utils/feature-flags.ts`
* Remove the env variable from all `.env` files
* Delete the old component/folder (V1)
* Remove the conditional branch — the new component becomes the only path
* Remove the test for the flag
* **This must happen within 2 weeks of full production rollout.** Stale flags are tech debt.


---

## Checklist for Code Review

When reviewing a PR that introduces a feature flag:

- [ ] Flag is defined in `utils/feature-flags.ts` (not ad-hoc in the component)
- [ ] Flag is checked in at most 1-3 places, all at container/parent level
- [ ] No flag value is passed as a prop to child components
- [ ] No flag check exists inside hooks or business logic
- [ ] No compound flag conditions (`FLAG_A && FLAG_B`)
- [ ] New component (V2) is in its own folder, not interleaved with the old component
- [ ] Old component is untouched (no modifications to accommodate the flag)
- [ ] Test added in `utils/__tests__/feature-flags.test.ts`
- [ ] `.env.example` updated


---

## FAQ

**Q: My V2 component shares 80% of the code with V1. Do I really duplicate it?** A: Yes. The duplication is temporary (2 weeks max after rollout). The alternative — weaving flags through shared code — is permanent complexity until cleanup, and cleanup becomes harder the more interleaved the flag is. Copy, modify, delete old. Simple.

**Q: What if my feature is just adding a new button to an existing page?** A: If the button is purely additive (no existing behavior changes), a simple conditional render at the point of insertion is fine:

```tsx
{FEATURE_FLAG.NEW_EXPORT_BUTTON && <ExportButton />}
```

This counts as one branch point. No component swap needed for pure additions.

**Q: Can I use feature flags for A/B testing?** A: Not with the current env-var-based system. Env vars are set at build/deploy time, not per-user. For A/B testing, a runtime feature flag service (LaunchDarkly, Unleash, etc.) would be needed. That is out of scope for now.

**Q: What if I need the flag in both frontend and backend?** A: Use the same flag name convention. Frontend uses `NEXT_PUBLIC_` prefix env vars. Backend uses its own flag system. They are independent. Coordinate the flag name in the ticket so both sides can be toggled together during rollout.
